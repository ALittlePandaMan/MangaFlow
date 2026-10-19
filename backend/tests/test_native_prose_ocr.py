from copy import deepcopy
from types import SimpleNamespace

import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services.base import OCRResult, ProviderState
from app.services.cloud_results import CloudResultCache, current_cloud_results
from app.services.ocr import MangaOCRProvider
from app.services.ocr.prose import native_boxes
from app.utils.geometry import bbox_to_polygon
from test_translation_auto_ocr import setup

BOX = [10, 10, 100, 100]
LINES = [[10, 10, 90, 20], [10, 45, 90, 20], [10, 80, 90, 20]]


def metadata():
    return {'ocr_frame_bbox': BOX[:], 'long_text_partition': {
        'direction': 'horizontal', 'ocr_boxes': deepcopy(LINES)}}


@pytest.mark.parametrize('change', ['none', 'moved', 'polygon', 'direction', 'empty', 'too_many', 'outside', 'invalid'])
def test_only_unchanged_verified_native_prose_uses_subcrops(change):
    region = SimpleNamespace(bbox=BOX[:], polygon=bbox_to_polygon(BOX), layout_data={'detection': metadata()})
    orientation = 'horizontal'
    if change == 'moved':
        region.bbox[0] += 10
    elif change == 'polygon':
        region.polygon[0][0] += 1
    elif change == 'direction':
        orientation = 'vertical'
    elif change == 'empty':
        region.layout_data['detection']['long_text_partition']['ocr_boxes'] = []
    elif change == 'too_many':
        region.layout_data['detection']['long_text_partition']['ocr_boxes'] = LINES*30
    elif change == 'outside':
        region.layout_data['detection']['long_text_partition']['ocr_boxes'][0][0] = 0
    elif change == 'invalid':
        region.layout_data['detection']['long_text_partition']['ocr_boxes'][0][0] = float('nan')
    assert native_boxes(region, orientation) == (LINES if change == 'none' else [region.bbox])


def seed(client):
    page, regions = setup(client, [''])
    with SessionLocal() as db:
        region = db.get(TextRegion, regions[0]['id'])
        region.bbox, region.polygon = BOX[:], bbox_to_polygon(BOX)
        region.orientation = 'horizontal'
        region.layout_data = {**region.layout_data, 'detection': metadata()}
        db.commit()
    return page, regions[0]['id']


def provider(monkeypatch, infer):
    model = MangaOCRProvider()
    model.state = ProviderState.READY
    monkeypatch.setattr(model, 'batch_size', lambda: 2)
    monkeypatch.setattr(model, 'recognize_batch', infer)
    monkeypatch.setattr(PipelineProcessor, '_provider', lambda *_: (model, 'manga-ocr'))


def test_native_prose_saves_one_region_after_all_crops_and_reuses_partial_results(client, monkeypatch, tmp_path):
    page, identifier = seed(client)
    calls, fail = [], [True]

    def infer(path, jobs, padding, **kwargs):
        calls.append([box[1] for box, _ in jobs])
        if jobs[0][0][1] == 80 and fail[0]:
            fail[0] = False
            raise RuntimeError('one native failure')
        return [OCRResult(str(int(box[1])), .8, orientation, {'confidence_available':False}) for box, orientation in jobs]

    provider(monkeypatch, infer)
    cache = CloudResultCache(tmp_path, tmp_path/'native-results')
    token = current_cloud_results.set(cache)
    try:
        with SessionLocal() as db:
            processor = PipelineProcessor(db)
            actual = db.get(ImagePage, page['id'])
            with pytest.raises(RuntimeError, match='one native failure'):
                processor.ocr(actual, None, force=True, region_id=None, options={'crop_padding':0})
            assert db.get(TextRegion, identifier).source_text == ''
            progress = []
            processor.ocr(actual, None, force=True, region_id=None, options={'crop_padding':0}, progress=lambda *args: progress.append(args))
            db.commit()
            saved = db.get(TextRegion, identifier)
            assert saved.source_text == '104580'
            assert saved.layout_data['ocr']['native_prose_chunks'] == 3
            assert max(row[0] for row in progress) == 1 and all(row[1] == 1 for row in progress)
        assert calls == [[10,45], [80], [80]]
    finally:
        current_cloud_results.reset(token)


def test_prose_padding_cannot_include_neighbouring_line_ink():
    lines = [[10,10,90,20],[10,33,90,20],[10,56,90,20]]
    region = SimpleNamespace(bbox=BOX[:], polygon=bbox_to_polygon(BOX), layout_data={'detection':metadata()})
    region.layout_data['detection']['long_text_partition']['ocr_boxes'] = lines
    boxes = native_boxes(region,'horizontal',4)
    for index,box in enumerate(boxes):
        assert box[1] <= lines[index][1] and box[1]+box[3] >= lines[index][1]+lines[index][3]
        for other, line in enumerate(lines):
            if other != index:
                assert box[1]+box[3] <= line[1] or box[1] >= line[1]+line[3]


def test_empty_native_prose_chunk_remains_visible_to_quality_review(client, monkeypatch):
    from app.services.quality import evaluate_page

    page, identifier = seed(client)

    def infer(path,jobs,padding,**kwargs):
        return [OCRResult('' if box[1]==45 else '文字',.8,orientation,{'confidence_available':False}) for box,orientation in jobs]

    provider(monkeypatch,infer)
    with SessionLocal() as db:
        processor=PipelineProcessor(db)
        actual=db.get(ImagePage,page['id'])
        processor.ocr(actual,None,force=True,region_id=None,options={'crop_padding':0})
        saved=db.get(TextRegion,identifier)
        assert saved.source_text=='文字文字' and 'incomplete_ocr_chunks' in saved.review_reasons
        assert any(issue.code=='incomplete_ocr_chunks' for issue in evaluate_page(actual))


def test_native_chunks_do_not_overwrite_a_user_edit_before_final_save(client, monkeypatch):
    page,identifier=seed(client)
    def infer(path,jobs,padding,**kwargs):
        if jobs[0][0][1]==80:
            with SessionLocal() as other:
                other.get(TextRegion,identifier).source_text='manual edit'
                other.commit()
        return [OCRResult('recognized',.8,orientation) for _,orientation in jobs]

    provider(monkeypatch,infer)
    with SessionLocal() as db:
        actual=db.get(ImagePage,page['id'])
        PipelineProcessor(db).ocr(actual,None,force=True,region_id=None,options={'crop_padding':0})
        db.expire_all()
        assert db.get(TextRegion,identifier).source_text=='manual edit'

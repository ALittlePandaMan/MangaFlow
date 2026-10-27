"""MCP frame corrections and mask restoration through the actual queue."""
import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, ProcessingTask, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services import mcp_workflows
from app.storage import get_storage
from app.tasks import task_manager
from PIL import Image, ImageDraw
from test_mcp import mutation, rpc
from test_mcp_workflow_recovery import patch, process, published
from test_mcp_workflows import begin, ok, review, state


@pytest.fixture(autouse=True)
def setup(client, monkeypatch):
    client.headers['Host'] = 'localhost'
    monkeypatch.setattr(mcp_workflows, 'blockers', lambda *args, **kwargs: [])
    original = PipelineProcessor._provider
    calls = []

    class Fill:
        def inpaint(self, image_path, mask_path, output_path, region_type):
            with Image.open(image_path) as source, Image.open(mask_path) as mask:
                calls.append(mask.getbbox())
                Image.composite(Image.new('RGB', source.size, 'white'), source.convert('RGB'), mask.convert('L')).save(output_path)
            return output_path

    monkeypatch.setattr(PipelineProcessor, '_provider', lambda processor, kind, requested:
        (Fill(), 'audit-fill') if kind == 'inpainting' else original(processor, kind, requested))
    return calls


def paint(client, image, text, run, points, mode='paint', shape='polygon'):
    return rpc(client, 'mask_paint', mutation(image_id=image['id'], region_id=text['region_key'],
        workflow_run_id=run, mode=mode, shape=shape, points=points,
        expected_versions=state(client, image)['expected_versions']))


def test_manual_mask_can_remove_missed_ink_outside_source_box(client):
    project, image, text, _ = published(client)
    with SessionLocal() as db:
        page = db.get(ImagePage, image['id'])
        original_path = get_storage().absolute(page.original_path)
        with Image.open(original_path) as opened:
            original = opened.convert('RGB')
        ImageDraw.Draw(original).rectangle((128, 50, 140, 70), fill='black')
        original.save(original_path)
    run = begin(client, project)
    ok(review(client, run, image, phase='preflight', outcome='passed', region_ids=[text['id']]))
    ok(paint(client, image, text, run, [[126, 48], [142, 48], [142, 72], [126, 72]]))
    with SessionLocal() as db:
        mask = get_storage().absolute(db.get(TextRegion, text['id']).pixel_mask_path)
        with Image.open(mask) as bitmap:
            assert bitmap.getpixel((135, 60)) == 255
            assert bitmap.getpixel((40, 60)) == 255  # existing brush data survives
    process(client, image, run)
    with SessionLocal() as db:
        page = db.get(ImagePage, image['id'])
        with Image.open(get_storage().absolute(page.clean_path)) as clean:
            assert clean.getpixel((135, 60)) == (255, 255, 255)
        assert db.get(TextRegion, text['id']).source_text == text['source_text']
        assert db.get(TextRegion, text['id']).layout_data['mask_generation']['method'] == 'manual_mask'


def test_mask_only_correction_does_not_repair_other_approved_regions(client):
    project, image, text, other = published(client, extra=True)
    with SessionLocal() as db:
        target = db.get(ImagePage, image['id'])
        other_row = db.get(TextRegion, other['id'])
        other_path = get_storage().absolute(other_row.pixel_mask_path)
        mask = Image.new('L', (300, 200), 0)
        ImageDraw.Draw(mask).rectangle((190, 50, 230, 70), fill=255)
        mask.save(other_path)
        clean_path = get_storage().absolute(target.clean_path)
        with Image.open(clean_path) as opened:
            clean = opened.convert('RGB')
        ImageDraw.Draw(clean).rectangle((190, 50, 230, 70), fill='blue')
        clean.save(clean_path)
    run = begin(client, project)
    ok(review(client, run, image, phase='preflight', outcome='passed', region_ids=[text['id'], other['id']]))
    ok(paint(client, image, text, run, [[32, 45], [50, 45], [50, 70], [32, 70]]))
    task = process(client, image, run)
    with SessionLocal() as db:
        payload = db.get(ProcessingTask, task['id']).payload
        with Image.open(get_storage().absolute(db.get(ImagePage, image['id']).clean_path)) as clean:
            assert clean.getpixel((200, 60)) == (0, 0, 255)
    assert payload['options']['region_ids'] == [text['id']], payload


@pytest.mark.parametrize('separate', [False, True])
def test_source_repair_preserves_explicit_translation_frame(client, separate):
    project, image, text, _ = published(client)
    run = begin(client, project)
    source = {'bbox': [25, 35, 100, 110], 'source_text': 'Verified source', 'translated_text': 'Verified translation'}
    translated = {'translated_bbox': [160, 50, 100, 70], 'rotation': 12, 'perspective_warp': True}
    patch(client, run, image, text, source if separate else {**source, **translated})
    if separate:
        patch(client, run, image, text, translated)
    before = state(client, image)['regions'][0]
    assert before['translated_bbox'] == translated['translated_bbox']
    ok(review(client, run, image, phase='preflight', outcome='passed', region_ids=[text['id']]))
    process(client, image, run)
    with SessionLocal() as db:
        stored = db.get(TextRegion, text['id'])
        assert stored.translated_bbox == translated['translated_bbox']
        assert stored.perspective_warp is True


@pytest.mark.parametrize('overlap', [False, True])
def test_erasing_then_preserving_region_restores_old_repair_pixels(client, setup, overlap):
    project, image, text, other = published(client, extra=overlap)
    with SessionLocal() as db:
        page = db.get(ImagePage, image['id'])
        clean_path = get_storage().absolute(page.clean_path)
        with Image.open(clean_path) as opened:
            clean = opened.convert('RGB')
        ImageDraw.Draw(clean).rectangle((35, 50, 75, 70), fill='red')
        if other:
            mask = Image.new('L', (300, 200), 0)
            ImageDraw.Draw(mask).rectangle((60, 50, 100, 70), fill=255)
            mask.save(get_storage().absolute(db.get(TextRegion, other['id']).pixel_mask_path))
            ImageDraw.Draw(clean).rectangle((60, 50, 100, 70), fill='blue')
        clean.save(clean_path)
    run = begin(client, project)
    ok(paint(client, image, text, run, [[0, 0], [299, 0], [299, 199], [0, 199]], mode='erase'))
    patch(client, run, image, text, {'translated_text': ''})
    ok(review(client, run, image, phase='preflight', outcome='passed', region_ids=[other['id']] if other else [],
        preserved=[{'region_id': text['region_key'], 'reason': 'False detection; restore artwork'}]))
    process(client, image, run)
    assert not setup  # Empty-mask restoration never invokes model inference.
    with SessionLocal() as db:
        page = db.get(ImagePage, image['id'])
        with Image.open(get_storage().absolute(page.clean_path)) as clean:
            assert clean.getpixel((40, 60)) == (255, 255, 255)
            if other:
                assert clean.getpixel((65, 60)) == (0, 0, 255)
        assert db.get(TextRegion, text['id']).layout_data['mask_generation']['repair_pending'] is False
    history = client.get(f"/api/projects/{project['id']}/history").json()
    undone = client.post(f"/api/projects/{project['id']}/history/undo", json={'expected_entry_id': history['undo_id']})
    assert undone.status_code == 200, undone.text
    with SessionLocal() as db:
        with Image.open(get_storage().absolute(db.get(ImagePage, image['id']).clean_path)) as clean:
            assert clean.getpixel((40, 60)) == (255, 0, 0)
    history = client.get(f"/api/projects/{project['id']}/history").json()
    redone = client.post(f"/api/projects/{project['id']}/history/redo", json={'expected_entry_id': history['redo_id']})
    assert redone.status_code == 200, redone.text
    ok(review(client, run, image, outcome='passed'))


@pytest.mark.parametrize('shape,points', [
    ('polygon', [[500, 500], [600, 500], [600, 600], [500, 600]]),
    ('polygon', [[-1, 10], [20, 10], [20, 20]]),
    ('stroke', [[290, 100], [310, 100]]),
])
def test_out_of_page_paint_does_not_succeed_as_empty_edit(client, shape, points):
    project, image, text, _ = published(client)
    run = begin(client, project)
    before = state(client, image)['version']
    response = paint(client, image, text, run, points, shape=shape)
    assert response.get('isError'), response
    assert response['structuredContent']['error']['status'] == 422
    assert state(client, image)['version'] == before


@pytest.mark.parametrize('mode,points', [
    ('paint', [[40, 55], [50, 55], [50, 65], [40, 65]]),
    ('erase', [[200, 150], [210, 150], [210, 160], [200, 160]]),
])
def test_unchanged_brush_preserves_version_render_and_history(client, mode, points):
    project, image, text, _ = published(client)
    run = begin(client, project)
    before = state(client, image)['version']
    history = client.get(f"/api/projects/{project['id']}/history").json()
    response = ok(paint(client, image, text, run, points, mode=mode))
    assert response['changed'] is False and response['changed_bounds'] is None
    assert response['version'] == before == state(client, image)['version']
    assert client.get(f"/api/projects/{project['id']}/history").json()['undo_id'] == history['undo_id']


def test_excluded_nonempty_mask_cannot_use_restoration_exception(client, monkeypatch):
    from app.application import enqueue_page_task
    from app.schemas.domain import ProcessRequest
    from app.services.mcp_context import expected_versions, workflow_action, workflow_run_id
    from fastapi import HTTPException

    project, image, text, _ = published(client)
    run = begin(client, project)
    ok(review(client, run, image, phase='preflight', outcome='passed', region_ids=[]))
    ok(paint(client, image, text, run, [[125, 50], [140, 50], [140, 70], [125, 70]]))
    version = expected_versions.set(state(client, image)['expected_versions'])
    workflow = workflow_run_id.set(run)
    action = workflow_action.set('workflow_advance')
    monkeypatch.setattr(task_manager, 'dispatch', lambda identifier: None)
    try:
        with SessionLocal() as db, pytest.raises(HTTPException) as error:
            enqueue_page_task(db, db.get(ImagePage, image['id']), 'rendering',
                ProcessRequest(options={'region_ids': [text['id']]}), start_stage='inpainting')
        assert error.value.status_code == 422
    finally:
        expected_versions.reset(version)
        workflow_run_id.reset(workflow)
        workflow_action.reset(action)


def test_cleared_exclusion_does_not_skip_first_repair_of_other_masks(client):
    project, image, text, other = published(client, extra=True)
    with SessionLocal() as db:
        page = db.get(ImagePage, image['id'])
        page.clean_path = page.rendered_path = page.text_layer_path = None
        page.metadata_json = {}
        row = db.get(TextRegion, other['id'])
        row.layout_data = {**row.layout_data, 'mask_generation': {'method': 'conservative_glyph'}}
        db.commit()
    run = begin(client, project)
    ok(paint(client, image, text, run, [[0, 0], [300, 0], [300, 200], [0, 200]], mode='erase'))
    patch(client, run, image, text, {'translated_text': ''})
    ok(review(client, run, image, phase='preflight', outcome='passed', region_ids=[other['id']],
        preserved=[{'region_id': text['region_key'], 'reason': 'Preserved artwork'}]))
    first = process(client, image, run)
    with SessionLocal() as db:
        assert db.get(ProcessingTask, first['id']).payload['options']['region_ids'] == [other['id']]
    process(client, image, run)
    assert ok(rpc(client, 'workflow_status', {'run_id': run}))['pages'][0]['execution_stage'] == 'final_review'

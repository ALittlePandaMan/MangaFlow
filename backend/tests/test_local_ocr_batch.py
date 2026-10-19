from __future__ import annotations

import sys
import threading
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services.base import DetectionResult, OCRResult, ProviderError, ProviderState
from app.services.ocr import providers
from app.services.ocr.cloud import CloudMultimodalOCRProvider
from app.services.ocr.providers import MangaOCRProvider
from PIL import Image
from sqlalchemy import select
from test_pipeline import solid_page, wait_for_task
from test_translation_auto_ocr import setup


@pytest.mark.parametrize('kind', ['local', 'cloud'])
@pytest.mark.parametrize('edit_during_redetection', [False, True])
def test_batch_recognition_followed_by_page_redetection_saves_new_ocr_results(client, monkeypatch, kind, edit_during_redetection):
    class Detector:
        def detect(self, _path):
            return [DetectionResult(bbox=[x, 10, 30, 70],
                                    polygon=[[x, 10], [x+30, 10], [x+30, 80], [x, 80]],
                                    confidence=.9, metadata={'detector': 'rfdetr'}) for x in (10, 60, 110)]

    detector = Detector()
    ocr = MangaOCRProvider()
    ocr.state = ProviderState.READY
    monkeypatch.setattr(ocr, 'batch_size', lambda: 2)
    calls = []
    redetecting = False
    changed = False
    edit_lock = threading.Lock()

    def concurrent_edit():
        nonlocal changed
        with edit_lock:
            if edit_during_redetection and redetecting and not changed:
                with SessionLocal() as other:
                    row = other.scalar(select(TextRegion).where(TextRegion.image_id == page['id'])
                                       .order_by(TextRegion.reading_order))
                    row.source_text = 'edited while redetection runs'
                    other.commit()
                changed = True

    def infer(source, jobs, padding, **kwargs):
        concurrent_edit()
        calls.append(len(jobs))
        return [OCRResult('recognized text', .9, orientation) for _, orientation in jobs]

    monkeypatch.setattr(ocr, 'recognize_batch', infer)
    if kind == 'cloud':
        class CloudOCR(CloudMultimodalOCRProvider):
            def ensure_loaded(self):
                pass

            def recognize_once(self, source, box, orientation, padding):
                concurrent_edit()
                calls.append(1)
                return OCRResult('recognized text', .9, orientation)

        ocr = CloudOCR({'base_url': 'https://redetect-test.invalid', 'model': 'test',
                        'max_concurrency': 2, 'retries': 0})
    original = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor, '_provider',
        lambda processor, kind, requested: (detector, 'test-detector') if kind == 'detection'
        else (ocr, 'test-manga-ocr') if kind == 'ocr' else original(processor, kind, requested))
    project = client.post('/api/projects', json={'name': 'batch-then-redetect'}).json()
    page = client.post(f"/api/projects/{project['id']}/images",
        files={'files': ('page.png', solid_page(), 'image/png')}).json()[0]
    tasks = client.post(f"/api/projects/{project['id']}/batch-process", json={
        'start_stage': 'detection', 'end_stage': 'ocr', 'force': False, 'only_unrecognized': True,
        'image_ids': [page['id']],
    }).json()
    assert len(tasks) == 1
    assert wait_for_task(client, tasks[0]['id'])['status'] == 'COMPLETED'
    first = client.get(f"/api/images/{page['id']}/regions").json()
    assert len(first) == 3
    assert all(r['source_text'] == 'recognized text' for r in first)
    assert client.patch(f"/api/regions/{first[0]['id']}", json={
        'locked': True, 'source_text': 'locked original', 'translated_text': 'old translation', 'font_size': 63,
    }).status_code == 200
    moved = list(first[1]['bbox'])
    moved[0] += 1
    assert client.patch(f"/api/regions/{first[1]['id']}", json={'bbox': moved}).status_code == 200
    # Also cover legacy frames with no detection provenance, and a hand-drawn
    # frame containing ALL model candidates. Neither may suppress fresh results.
    with SessionLocal() as other:
        legacy = other.get(TextRegion, first[2]['id'])
        legacy.layout_data = {}
        other.commit()
    assert client.post(f"/api/images/{page['id']}/regions", json={
        'bbox': [0, 0, 180, 100], 'source_text': 'hand-drawn original', 'translated_text': 'hand-drawn translation',
    }).status_code == 201
    previous = client.get(f"/api/images/{page['id']}/regions").json()
    redetecting = True
    task = client.post(f"/api/images/{page['id']}/process", json={
        'start_stage': 'detection', 'end_stage': 'ocr', 'force': True, 'options': {'crop_padding': 4},
    }).json()
    from app.core.config import get_settings
    first_result = wait_for_task(client,task['id'])
    fenced = get_settings().checkpoint_pipeline_enabled and edit_during_redetection
    if fenced:
        assert first_result['status']=='FAILED' and '发生变化' in first_result['error_message']
        retained = client.get(f"/api/images/{page['id']}/regions").json()
        assert {row['id'] for row in retained}=={row['id'] for row in previous}
        assert any(row['source_text']=='edited while redetection runs' for row in retained)
        # A NEW explicit redetection still rebuilds every frame and OCR. An
        # external-writer conflict is not permission to silently overwrite it.
        task = client.post(f"/api/images/{page['id']}/process",json={
            'start_stage':'detection','end_stage':'ocr','force':True,'options':{'crop_padding':4}}).json()
        assert wait_for_task(client,task['id'])['status']=='COMPLETED'
    else:
        assert first_result['status']=='COMPLETED',first_result
    replaced = client.get(f"/api/images/{page['id']}/regions").json()
    assert len(replaced) == 3
    assert not {r['id'] for r in previous} & {r['id'] for r in replaced}
    assert all(r['source_text'] == 'recognized text' for r in replaced)
    assert all('ocr' in r['layout_data'] for r in replaced)
    assert all(not r['locked'] and not r['layout_data'].get('manual') and r['translated_text'] == '' for r in replaced)
    assert changed == edit_during_redetection
    assert calls == ([2,1]*(3 if fenced else 2) if kind=='local' else [1]*(9 if fenced else 6))
    if kind == 'local' and not edit_during_redetection:
        from test_editor_history import apply as apply_history

        fields = ['id', 'bbox', 'polygon', 'source_text', 'translated_text', 'locked', 'font_size', 'layout_data']
        def snapshot(rows):
            return {r['id']: {field: r[field] for field in fields} for r in rows}
        restored = apply_history(client, project['id'])['regions']
        assert snapshot(restored) == snapshot(previous)
        redone = apply_history(client, project['id'], 'redo')['regions']
        assert snapshot(redone) == snapshot(replaced)


def test_empty_forced_redetection_replaces_old_manual_and_locked_frames_with_no_ocr_jobs(client, monkeypatch):
    page, regions = setup(client, ['manual text', 'locked text'])
    assert client.patch(f"/api/regions/{regions[1]['id']}", json={'locked': True}).status_code == 200
    class EmptyDetector:
        def detect(self, _path):
            return []
    class OCR:
        def ensure_loaded(self):
            pytest.fail('No OCR model should load when redetection finds no frames')
    monkeypatch.setattr(PipelineProcessor, '_provider',
        lambda _, kind, name: (EmptyDetector(), 'empty') if kind == 'detection' else (OCR(), 'unused'))
    task = client.post(f"/api/images/{page['id']}/process", json={
        'start_stage': 'detection', 'end_stage': 'ocr', 'force': True,
    }).json()
    assert wait_for_task(client, task['id'])['status'] == 'COMPLETED'
    assert client.get(f"/api/images/{page['id']}/regions").json() == []


@pytest.mark.parametrize('mode', ['inverted_supplement', 'unknown'])
def test_retired_detection_mode_cannot_silently_rebuild_existing_frames(client, monkeypatch, mode):
    page, regions = setup(client, ['keep original'])
    lookup = Mock(side_effect=AssertionError('No model should run for an unsupported detection mode'))
    monkeypatch.setattr(PipelineProcessor, '_provider', lookup)
    task = client.post(f"/api/images/{page['id']}/process", json={
        'start_stage': 'detection', 'end_stage': 'ocr', 'force': True,
        'options': {'detection_mode': mode},
    }).json()
    result = wait_for_task(client, task['id'])
    assert result['status'] == 'FAILED'
    assert '不支持此检测模式' in result['error_message']
    lookup.assert_not_called()
    current = client.get(f"/api/images/{page['id']}/regions").json()
    assert [r['id'] for r in current] == [r['id'] for r in regions]
    assert current[0]['source_text'] == 'keep original'


def ready(monkeypatch):
    provider = MangaOCRProvider()
    provider.state = ProviderState.READY
    provider._device = "cuda:0"
    provider._model = lambda crop: str(crop.getpixel((0, 0))[0])
    monkeypatch.setattr(provider, "batch_size", lambda: provider._batch_ceiling)
    monkeypatch.setattr(providers, "_crop", lambda path, box, padding: Image.new("RGB", (4, 5), (int(box[0]), 0, 0)))
    return provider


def test_batch_size_tracks_memory_and_oom_ceiling(monkeypatch):
    state = {"free": 5 * 1024**3}
    torch = SimpleNamespace(cuda=SimpleNamespace(mem_get_info=lambda device: (state["free"], 8 * 1024**3)))
    monkeypatch.setitem(sys.modules, "torch", torch)
    provider = MangaOCRProvider()
    assert provider.batch_size() == 1
    provider._device = "cuda:0"
    assert provider.batch_size() == 8
    provider._batch_ceiling = 4
    assert provider.batch_size() == 4
    state["free"] = int(2.4 * 1024**3)
    assert provider.batch_size() == 1  # Keep at least 2 GiB free for other models.
    state["free"] = 1024**3
    assert provider.batch_size() == 1
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace())
    assert provider.batch_size() == 1  # Optional driver probing must not fail OCR.


@pytest.mark.parametrize("configured,expected", [(None, 8), ("", 8), (4, 4), ("2", 2), (32, 8), (0, 1), ("auto", 8)])
def test_configured_batch_size_is_an_upper_bound(configured, expected):
    assert MangaOCRProvider({"batch_size": configured})._batch_ceiling == expected


def test_batch_generation_matches_single_crop_preprocessing_and_parameters(monkeypatch):
    seen = []
    tensors = SimpleNamespace(to=lambda device: (device, "pixels"))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        inference_mode=nullcontext, stack=lambda values: (seen.extend(values), tensors)[1]))
    monkeypatch.setitem(sys.modules, "manga_ocr.ocr", SimpleNamespace(post_process=lambda text: text.replace(" ", "")))
    generated = SimpleNamespace(cpu=lambda: ["a", "b"])

    def generate(inputs, **kwargs):
        assert inputs == ("cuda:0", "pixels")
        assert kwargs == {"max_length": 300}
        return generated

    provider = MangaOCRProvider()
    provider._model = SimpleNamespace(
        _preprocess=lambda crop: (crop.mode, crop.getpixel((0, 0))),
        model=SimpleNamespace(device="cuda:0", generate=generate),
        tokenizer=SimpleNamespace(batch_decode=lambda ids, **kwargs: [" 文 字 ", " 訳 文 "]),
    )
    assert provider._recognize_crops([Image.new("RGB", (4, 5), "red"), Image.new("RGB", (4, 5), "blue")]) == ["文字", "訳文"]
    assert seen == [("RGB", (76, 76, 76)), ("RGB", (29, 29, 29))]


def test_oom_retries_smaller_batches_preserving_order_and_orientation(monkeypatch, tmp_path):
    provider = ready(monkeypatch)
    sizes = []

    def infer(crops):
        sizes.append(len(crops))
        if len(crops) > 2:
            raise RuntimeError("CUDA out of memory")
        return [str(crop.getpixel((0, 0))[0]) for crop in crops]

    monkeypatch.setattr(provider, "_recognize_crops", infer)
    released = []
    monkeypatch.setattr(providers, "release_torch_cuda", lambda: released.append(True))
    jobs = [([i, 0, 4, 5], "vertical" if i % 2 else "horizontal") for i in range(8)]
    results = provider.recognize_batch(tmp_path / "page.png", jobs)
    assert [r.text for r in results] == list(map(str, range(8)))
    assert [r.orientation for r in results] == [orientation for _, orientation in jobs]
    assert sizes == [8, 4, 2, 2, 2, 2] and len(released) == 2
    assert provider._batch_ceiling == 2
    assert all(r.metadata["inference_batch_size"] == 2 for r in results)
    assert provider.recognize_batch(tmp_path / "page.png", []) == []


def test_non_memory_gpu_error_reuses_single_crop_fallback(monkeypatch, tmp_path):
    provider = ready(monkeypatch)
    monkeypatch.setattr(providers, "release_torch_cuda", lambda: None)
    monkeypatch.setattr(provider, "_recognize_crops", lambda crops: (_ for _ in ()).throw(RuntimeError("CUDA device error")))
    results = provider.recognize_batch(tmp_path / "page.png", [([i, 0, 4, 5], "vertical") for i in range(3)])
    assert [r.text for r in results] == ["0", "1", "2"]
    assert provider._batch_ceiling == 1


def test_oom_fallback_checks_cancellation_between_smaller_generations(monkeypatch, tmp_path):
    provider = ready(monkeypatch)
    monkeypatch.setattr(providers, "release_torch_cuda", lambda: None)
    sizes = []
    checks = 0

    def infer(crops):
        sizes.append(len(crops))
        if len(crops) > 2:
            raise RuntimeError("CUDA out of memory")
        return ["text"] * len(crops)

    def check():
        nonlocal checks
        checks += 1
        if checks == 6:
            raise RuntimeError("cancelled")

    monkeypatch.setattr(provider, "_recognize_crops", infer)
    with pytest.raises(RuntimeError, match="cancelled"):
        provider.recognize_batch(tmp_path / "page.png", [([i, 0, 4, 5], "vertical") for i in range(8)], check=check)
    assert sizes == [8, 4, 2]


def test_invalid_generation_is_not_silently_truncated_or_retried(monkeypatch, tmp_path):
    provider = ready(monkeypatch)
    jobs = [([i, 0, 4, 5], "vertical") for i in range(2)]
    monkeypatch.setattr(provider, "_recognize_crops", lambda crops: ["missing result"])
    with pytest.raises(ProviderError, match="数量不匹配"):
        provider.recognize_batch(tmp_path / "page.png", jobs)
    monkeypatch.setattr(provider, "_recognize_crops", lambda crops: (_ for _ in ()).throw(ValueError("bad image")))
    with pytest.raises(ValueError, match="bad image"):
        provider.recognize_batch(tmp_path / "page.png", jobs)


def pipeline_provider(monkeypatch, infer, size=2):
    provider = MangaOCRProvider()
    provider.state = ProviderState.READY
    monkeypatch.setattr(provider, "batch_size", lambda: size)
    monkeypatch.setattr(provider, "recognize_batch", infer)
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda *_: (provider, "test-manga-ocr"))
    return provider


def test_pipeline_microbatches_only_pending_regions_and_keeps_each_result_durable(client, monkeypatch):
    page, regions = setup(client, ["", "keep source", "", "", "", ""])
    client.patch(f"/api/regions/{regions[-1]['id']}", json={"locked": True})
    batches = []

    def infer(source, jobs, padding, **kwargs):
        assert padding == 7
        batches.append([box[0] for box, _ in jobs])
        return [OCRResult(str(box[0]), .8, orientation, {"confidence_available": False}) for box, orientation in jobs]

    pipeline_provider(monkeypatch, infer)
    progress = []
    with SessionLocal() as db:
        stored = db.get(ImagePage, page["id"])
        PipelineProcessor(db).ocr(stored, None, force=False, region_id=None, options={"crop_padding": 7},
                                  progress=lambda done, total, message: progress.append(done))
    rows = {r["id"]: r for r in client.get(f"/api/images/{page['id']}/regions").json()}
    assert batches == [[10, 150], [220, 290]]
    assert rows[regions[1]["id"]]["source_text"] == "keep source"
    assert rows[regions[-1]["id"]]["source_text"] == ""
    assert [rows[regions[i]["id"]]["source_text"] for i in [0, 2, 3, 4]] == ["10.0", "150.0", "220.0", "290.0"]
    assert progress[-4:] == [1, 2, 3, 4]


@pytest.mark.parametrize("change", ["edit", "lock", "delete", "geometry"])
def test_pipeline_late_batch_result_cannot_overwrite_user_changes(client, monkeypatch, change):
    page, regions = setup(client, ["", ""])

    def infer(source, jobs, padding, **kwargs):
        with SessionLocal() as other:
            row = other.get(TextRegion, regions[0]["id"])
            if change == "delete":
                other.delete(row)
            elif change == "lock":
                row.locked = True
            elif change == "geometry":
                row.bbox = [11, 11, 45, 70]
            else:
                row.source_text = "user edit"
            other.commit()
        return [OCRResult("recognized", .8, orientation, {"confidence_available": False}) for _, orientation in jobs]

    pipeline_provider(monkeypatch, infer)
    with SessionLocal() as db:
        PipelineProcessor(db).ocr(db.get(ImagePage, page["id"]), None, force=True, region_id=None, options={})
    rows = {r["id"]: r for r in client.get(f"/api/images/{page['id']}/regions").json()}
    assert rows[regions[1]["id"]]["source_text"] == "recognized"
    if change == "delete":
        assert regions[0]["id"] not in rows
    else:
        assert rows[regions[0]["id"]]["source_text"] == ("user edit" if change == "edit" else "")


def test_cancel_does_not_dispatch_next_microbatch_or_discard_already_saved_results(client, monkeypatch):
    page, regions = setup(client, ["", "", ""])
    calls = []

    def infer(source, jobs, padding, **kwargs):
        calls.append(len(jobs))
        return [OCRResult("saved", .8, orientation) for _, orientation in jobs]

    pipeline_provider(monkeypatch, infer)

    def progress(done, total, message):
        if done == 2:
            raise RuntimeError("cancelled")

    with SessionLocal() as db, pytest.raises(RuntimeError, match="cancelled"):
        PipelineProcessor(db).ocr(db.get(ImagePage, page["id"]), None, force=True, region_id=None, options={}, progress=progress)
    assert calls == [2]
    rows = {r["id"]: r for r in client.get(f"/api/images/{page['id']}/regions").json()}
    assert [rows[r["id"]]["source_text"] for r in regions] == ["saved", "saved", ""]

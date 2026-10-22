from __future__ import annotations

import threading
import time

import pytest
from app.pipeline.processor import PipelineProcessor
from app.services.base import OCRResult, ProviderError
from app.services.ocr.cloud import CloudMultimodalOCRProvider
from test_editor_history import apply as apply_history
from test_pipeline import solid_page, wait_for_task


@pytest.fixture(params=["local", "cloud"])
def providers(monkeypatch, request):
    class RecordingOCR:
        def __init__(self):
            self.calls = []
            self.responses = {}
            self.loaded = 0

        def ensure_loaded(self):
            self.loaded += 1

        def recognize(self, source, bbox, orientation, padding, *, polygon=None, crop_angle=None):
            self.calls.append((list(bbox), orientation, padding))
            value = self.responses.get(bbox[0], "识别的原文")
            if isinstance(value, Exception):
                raise value
            return OCRResult(value, .99, orientation, {"test": True})

    recorder = RecordingOCR()
    if request.param == "cloud":
        class FakeCloud(CloudMultimodalOCRProvider):
            def ensure_loaded(self):
                recorder.ensure_loaded()

            def recognize_once(self, *args, **kwargs):
                return recorder.recognize(*args, **kwargs)

        ocr = FakeCloud({"base_url": "https://auto-ocr-test.invalid", "model": "fake", "retries": 0, "max_concurrency": 2})
    else:
        ocr = recorder

    class RecordingTranslator:
        config = {}
        calls = []

        async def translate_regions(self, rows, **kwargs):
            self.calls.append((rows, kwargs["context"]))
            return {key: f"译文:{text}" for key, text in rows}

    translator = RecordingTranslator()
    lookups = []
    original = PipelineProcessor._provider

    def lookup(processor, kind, requested):
        lookups.append((kind, requested))
        if kind == "ocr":
            return ocr, "test-ocr"
        if kind == "translation":
            return translator, "test-translator"
        return original(processor, kind, requested)

    monkeypatch.setattr(PipelineProcessor, "_provider", lookup)
    return recorder, translator, lookups


def setup(client, sources):
    project = client.post("/api/projects", json={"name": "translation-auto-ocr"}).json()
    page = client.post(f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", solid_page(500, 120), "image/png")}).json()[0]
    regions = []
    for index, source in enumerate(sources):
        left = 10 + index * 70
        region = client.post(f"/api/images/{page['id']}/regions", json={
            "polygon": [[left, 10], [left + 50, 10], [left + 50, 90], [left, 90]],
            "source_text": source,
        }).json()
        regions.append(region)
    return page, regions


def run_translation(client, page, regions, scope, **extra):
    payload = {"start_stage": "translation", "end_stage": "translation", "force": True, **extra}
    if scope == "single":
        task = client.post(f"/api/regions/{regions[0]['id']}/translate", json=payload)
    else:
        if scope == "multi":
            payload["options"] = {**payload.get("options", {}), "region_ids": [region["id"] for region in regions]}
        task = client.post(f"/api/images/{page['id']}/process", json=payload)
    assert task.status_code == 202, task.text
    result = wait_for_task(client, task.json()["id"])
    rows = client.get(f"/api/images/{page['id']}/regions").json()
    return result, {row["id"]: row for row in rows}


@pytest.mark.parametrize("scope", ["single", "multi", "page"])
def test_translation_recognizes_only_empty_source_in_its_scope(client, providers, scope):
    ocr, translator, lookups = providers
    page, regions = setup(client, [" \n\u3000", "已有原文", ""])
    result, rows = run_translation(client, page, regions[:2], scope, provider="selected-translator",
        options={"crop_padding": 7})
    assert result["status"] == "COMPLETED", result
    expected_ocr = [regions[0], regions[2]] if scope == "page" else [regions[0]]
    assert sorted(call[0] for call in ocr.calls) == sorted(region["bbox"] for region in expected_ocr)
    assert all(call[2] == 7 for call in ocr.calls)
    assert ("ocr", None) in lookups  # Never reuse the translation provider for OCR.
    assert ("translation", "selected-translator") in lookups
    assert len(translator.calls) == 1
    expected_regions = regions if scope == "page" else regions[:1] if scope == "single" else regions[:2]
    assert dict(translator.calls[0][0]) == {
        region["region_key"]: ("已有原文" if region == regions[1] else "识别的原文") for region in expected_regions
    }
    for region in expected_ocr:
        assert rows[region["id"]]["source_text"] == "识别的原文"
    for region in regions:
        assert rows[region["id"]]["polygon"] == region["polygon"]
        assert rows[region["id"]]["translated_polygon"] == region["translated_polygon"]
    if scope != "page":
        assert rows[regions[2]["id"]]["source_text"] == ""
        assert rows[regions[2]["id"]]["translated_text"] == ""
    context = {row["id"]: row["source"] for row in translator.calls[0][1]["page_context"]}
    assert context[regions[0]["region_key"]] == "识别的原文"


@pytest.mark.parametrize("existing", ["", "已有原文"])
def test_empty_ocr_is_attempted_once_then_skipped_without_empty_translation(client, providers, existing):
    ocr, translator, _ = providers
    page, regions = setup(client, ["", existing])
    ocr.responses = {region["bbox"][0]: " \n" for region in regions}
    result, rows = run_translation(client, page, regions, "page")
    assert result["status"] == "COMPLETED", result
    assert len(ocr.calls) == (1 if existing else 2)
    assert rows[regions[0]["id"]]["translated_text"] == ""
    assert "empty_source_text" in rows[regions[0]["id"]]["review_reasons"]
    assert len(translator.calls) == (1 if existing else 0)
    if existing:
        assert translator.calls[0][0] == [(regions[1]["region_key"], existing)]


def test_ocr_translation_pipeline_does_not_retry_empty_region_in_same_task(client, providers):
    ocr, translator, _ = providers
    page, regions = setup(client, [""])
    ocr.responses[regions[0]["bbox"][0]] = ""
    result, _ = run_translation(client, page, regions, "page", start_stage="ocr")
    assert result["status"] == "COMPLETED", result
    assert len(ocr.calls) == 1
    assert not translator.calls


def test_existing_source_skips_ocr_and_nonforced_existing_translation_stays_untouched(client, providers):
    ocr, translator, lookups = providers
    page, regions = setup(client, ["已有原文", ""])
    response = client.patch(f"/api/regions/{regions[1]['id']}", json={"translated_text": "人工译文"})
    assert response.status_code == 200
    result, rows = run_translation(client, page, regions, "page", force=False)
    assert result["status"] == "COMPLETED", result
    assert not ocr.calls and not ocr.loaded
    assert ("ocr", None) not in lookups
    assert translator.calls[0][0] == [(regions[0]["region_key"], "已有原文")]
    assert rows[regions[1]["id"]]["translated_text"] == "人工译文"


@pytest.mark.parametrize("scope", ["page", "single", "multi"])
def test_auto_ocr_preserves_existing_lock_rules(client, providers, scope):
    ocr, translator, _ = providers
    page, regions = setup(client, [""])
    client.patch(f"/api/regions/{regions[0]['id']}", json={"locked": True})
    result, rows = run_translation(client, page, regions, scope)
    assert result["status"] == "COMPLETED", result
    assert len(ocr.calls) == (0 if scope == "page" else 1)
    assert len(translator.calls) == (0 if scope == "page" else 1)
    assert rows[regions[0]["id"]]["locked"] is True


def test_auto_ocr_failure_does_not_submit_translation(client, providers):
    ocr, translator, _ = providers
    page, regions = setup(client, [""])
    ocr.responses[regions[0]["bbox"][0]] = ProviderError("OCR test failed")
    result, rows = run_translation(client, page, regions, "single")
    assert result["status"] == "FAILED", result
    assert "OCR test failed" in result["error_message"]
    assert not translator.calls
    assert rows[regions[0]["id"]]["source_text"] == ""


def test_auto_ocr_and_translation_undo_as_one_operation(client, providers):
    _, translator, _ = providers
    page, regions = setup(client, [""])
    result, rows = run_translation(client, page, regions, "single")
    assert result["status"] == "COMPLETED", result
    assert rows[regions[0]["id"]]["translated_text"] == "译文:识别的原文"
    undone = apply_history(client, page["project_id"])["regions"][0]
    assert undone["source_text"] == undone["translated_text"] == ""
    redone = apply_history(client, page["project_id"], "redo")["regions"][0]
    assert redone["source_text"] == "识别的原文"
    assert redone["translated_text"] == "译文:识别的原文"
    assert len(translator.calls) == 1


def test_auto_ocr_reports_translation_substep_and_honors_cancellation(client, providers, monkeypatch):
    ocr, translator, _ = providers
    page, regions = setup(client, [""])
    started, release = threading.Event(), threading.Event()
    recognize = ocr.recognize

    def blocking_recognize(*args, **kwargs):
        started.set()
        assert release.wait(timeout=5)
        return recognize(*args, **kwargs)

    monkeypatch.setattr(ocr, "recognize", blocking_recognize)
    response = client.post(f"/api/regions/{regions[0]['id']}/translate", json={"force": True})
    assert response.status_code == 202, response.text
    task_id = response.json()["id"]
    try:
        assert started.wait(timeout=3)
        running = client.get(f"/api/tasks/{task_id}").json()
        from app.core.config import get_settings
        assert running["current_stage"] == ("ocr" if get_settings().checkpoint_pipeline_enabled else "translation")
        assert running["message"].startswith("翻译前自动识别")
        assert running["progress"] < 1
        cancelled = client.post(f"/api/tasks/{task_id}/cancel")
        assert cancelled.status_code == 200, cancelled.text
    finally:
        release.set()
    for _ in range(100):
        task = client.get(f"/api/tasks/{task_id}").json()
        if task["status"] in {"CANCELLED", "FAILED", "COMPLETED"}:
            break
        time.sleep(.03)
    assert task["status"] == "CANCELLED", task
    assert not translator.calls

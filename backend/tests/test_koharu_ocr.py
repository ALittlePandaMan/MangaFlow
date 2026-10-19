from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.services.base import ProviderError, ProviderState
from app.services.ocr import local
from app.services.ocr.local_catalog import LOCAL_OCR_MODELS, LocalOCRModel, validate_local_config, verify_code
from app.services.ocr.local_worker import validated_crop
from app.services.registry import ProviderRegistry


@pytest.mark.parametrize("name", list(LOCAL_OCR_MODELS))
def test_koharu_providers_registered_and_do_not_load_models_when_listed(name, monkeypatch):
    registry = ProviderRegistry()
    monkeypatch.setattr(local, "resolve_assets", lambda *a, **k: pytest.fail("listing must not download models"))
    descriptor = next(item for item in registry.describe() if item["name"] == name)
    assert descriptor["kind"] == "ocr"
    assert descriptor["devices"] == ["cpu", "cuda"]
    assert descriptor["capabilities"]["koharu_aligned"]
    provider = registry.create("ocr", name, {"device": "cpu"})
    assert provider.state == ProviderState.UNLOADED
    assert provider.config["revision"] == LOCAL_OCR_MODELS[name].revision


@pytest.mark.parametrize("config", [{"max_new_tokens": True}, {"max_new_tokens": 1025}, {"max_new_tokens": float("nan")},
                                    {"timeout": 0}, {"timeout": 30.5}, {"model": "untrusted/model"},
                                    {"revision": "main"}, {"device": "cuda:99"}])
def test_local_ocr_rejects_unsafe_or_unbounded_config(config):
    with pytest.raises(ValueError):
        validate_local_config("paddleocr-vl-1.6", config)


def test_custom_code_must_match_pinned_checksum(tmp_path):
    path = tmp_path / "model.py"
    path.write_bytes(b"approved\n")
    model = LocalOCRModel("repo", "fixed", ("model.py",), (("model.py", hashlib.sha256(path.read_bytes()).hexdigest()),))
    verify_code(tmp_path, model)
    path.write_bytes(b"changed\n")
    with pytest.raises(ValueError, match="checksum"):
        verify_code(tmp_path, model)


def test_runtime_reused_when_only_generation_options_change():
    registry = ProviderRegistry()
    first = registry.create("ocr", "paddleocr-vl-1.6", {"device": "cpu", "max_new_tokens": 128})
    second = registry.create("ocr", "paddleocr-vl-1.6", {"device": "cpu", "max_new_tokens": 256})
    different = registry.create("ocr", "hayai-ocr", {"device": "cpu"})
    assert first.worker is second.worker
    assert first.worker is not different.worker


def test_local_ocr_preserves_crop_and_does_not_report_fake_confidence(monkeypatch):
    monkeypatch.setattr(local, "resolve_assets", lambda *a, **k: (Path("/fixed/model"), None))
    provider = local.PaddleOCRVLProvider({"device": "cpu", "max_new_tokens": 128})
    calls = []
    monkeypatch.setattr(provider.worker, "start", lambda *args: setattr(provider.worker, "actual_device", "cpu"))
    def recognize(*args):
        from app.services.execution import execution_runtime
        assert execution_runtime().model_active == 1
        calls.append(args)
        return {"text": "007", "truncated": True}
    monkeypatch.setattr(provider.worker, "recognize", recognize)
    box = [10., 20., 30., 12.]
    result = provider.recognize(Path("/original.png"), box, "horizontal", 0)
    assert result.text == "007"
    assert result.orientation == "horizontal"
    assert result.metadata["confidence_available"] is False
    assert result.metadata["generation_truncated"] is True
    assert result.metadata["revision"] == LOCAL_OCR_MODELS["paddleocr-vl-1.6"].revision
    assert calls == [(Path("/original.png"), box, "horizontal", 0, 128, 180)]
    assert box == [10., 20., 30., 12.]


def test_shared_worker_unload_causes_ready_provider_to_reload(monkeypatch):
    monkeypatch.setattr(local, "resolve_assets", lambda *a, **k: (Path("/fixed/model"), None))
    provider = local.PaddleOCRVLProvider({"device": "cpu"})
    provider.state = ProviderState.READY
    assert provider.worker.process is None
    loaded = []
    monkeypatch.setattr(provider.worker, "start", lambda *a: loaded.append(True))
    monkeypatch.setattr(provider.worker, "recognize", lambda *a: {"text": "007"})
    assert provider.recognize(Path("/original.png"), [0, 0, 20, 20], "horizontal").text == "007"
    assert loaded == [True]


def test_failed_ocr_fails_closed_and_clears_worker(monkeypatch):
    provider = local.HayaiOCRProvider({"device": "cpu"})
    monkeypatch.setattr(provider, "ensure_loaded", lambda: None)
    monkeypatch.setattr(provider.worker, "recognize", lambda *args: (_ for _ in ()).throw(RuntimeError("bad worker")))
    closed = []
    monkeypatch.setattr(provider.worker, "close", lambda: closed.append(True))
    with pytest.raises(ProviderError, match="未覆盖原文"):
        provider.recognize(Path("/original.png"), [0, 0, 20, 20], "horizontal")
    assert closed
    assert provider.state == ProviderState.ERROR


@pytest.mark.parametrize("box", [[0, 0, 0, 10], [0, 0, float("nan"), 10], [1, 2, 3], [False, 0, 1, 1]])
def test_worker_rejects_invalid_crop_before_reading_image(box):
    with pytest.raises(ValueError, match="crop"):
        validated_crop({"bbox": box, "image_path": "/does-not-exist"})


def test_api_can_select_koharu_ocr_without_changing_detection(client):
    before = client.get("/api/models").json()["configured"]
    result = client.post("/api/models/bootstrap", json={"preload": False, "selections": [
        {"kind": "ocr", "provider": "paddleocr-vl-1.6", "device": "cpu"}]})
    assert result.status_code == 200, result.text
    after = client.get("/api/models").json()["configured"]
    assert [m for m in before if m["kind"] != "ocr"] == [m for m in after if m["kind"] != "ocr"]
    defaults = [m for m in after if m["kind"] == "ocr" and m["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["provider"] == "paddleocr-vl-1.6"
    invalid = client.post("/api/models/config", json={"kind": "ocr", "provider": "hayai-ocr", "name": "bad model",
                                                   "config": {"model": "arbitrary/repo"}})
    assert invalid.status_code == 422


def test_truncated_local_ocr_is_marked_for_review():
    from app.pipeline.processor import PipelineProcessor
    from app.services.base import OCRResult

    region = SimpleNamespace(review_reasons=[], layout_data={})
    # Inspect the write through a lightweight fake rather than run inference.
    processor = object.__new__(PipelineProcessor)
    processor.settings = SimpleNamespace(ocr_review_threshold=.65)
    captured = {}
    class Session:
        def add(self, value):
            pass
    processor.db = Session()
    # The complete persistence/CAS path has separate pipeline tests; assert its
    # review values via the no-CAS path, with revision creation suppressed.
    from unittest.mock import patch

    import app.pipeline.processor as module
    with patch.object(module, "save_revision"), patch.object(processor, "_initialize_text_rotation", return_value=False):
        result = OCRResult("part", .8, "horizontal", {"confidence_available": False, "generation_truncated": True})
        region.id = "test"
        processor._apply_ocr_result(region, result)
        captured.update(region.__dict__)
    assert "ocr_generation_truncated" in captured["review_reasons"]

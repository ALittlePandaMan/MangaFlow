"""Automatic device policy must fall back only for accelerator faults."""
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.services.base import ProviderError, ProviderState
from app.services.detection.classification import LayoutTextClassifier
from app.services.inpainting.providers import HybridInpainter
from app.services.ocr import local


@pytest.mark.parametrize("provider", [local.PaddleOCRVLProvider, local.BaberuOCRProvider, local.HayaiOCRProvider])
def test_local_ocr_auto_retries_gpu_initialization_on_cpu(provider, monkeypatch):
    monkeypatch.setattr(local, "resolve_assets", lambda *a, **k: (Path("/model"), None))
    instance = provider({"device": "auto"})
    calls = []

    def start(*args):
        calls.append(instance.worker.effective_device)
        if instance.worker.effective_device == "auto":
            raise RuntimeError("CUDA initialization failed")
        instance.worker.actual_device = "cpu"

    monkeypatch.setattr(instance.worker, "start", start)
    instance.ensure_loaded()
    assert calls == ["auto", "cpu"]
    assert instance.config["device"] == "auto"
    assert instance._device == "cpu"
    assert instance._fallback_reason == "accelerator_initialization_failed"
    assert instance.state == ProviderState.READY
    assert instance.worker.device == "auto"
    instance.unload()
    assert instance.worker.effective_device == "auto"


def test_local_ocr_auto_retries_inference_once_without_changing_crop(monkeypatch):
    monkeypatch.setattr(local, "resolve_assets", lambda *a, **k: (Path("/model"), None))
    instance = local.PaddleOCRVLProvider({"device": "auto"})
    calls = []

    def start(*args):
        instance.worker.actual_device = "cpu" if instance.worker.effective_device == "cpu" else "cuda:0"
        instance.worker.process = SimpleNamespace(poll=lambda: None)

    def recognize(*args):
        calls.append((instance.worker.actual_device, args))
        if instance.worker.actual_device == "cuda:0":
            # The real worker closes itself on a failed request too.
            instance.worker.actual_device = None
            raise RuntimeError("CUDA out of memory")
        return {"text": "007"}

    monkeypatch.setattr(instance.worker, "start", start)
    monkeypatch.setattr(instance.worker, "close", lambda: setattr(instance.worker, "process", None))
    monkeypatch.setattr(instance.worker, "recognize", recognize)
    result = instance.recognize(Path("/original.png"), [1, 2, 20, 30], "vertical", 0)
    assert result.text == "007"
    assert result.metadata["device"] == "cpu"
    assert instance._fallback_reason == "accelerator_inference_failed"
    assert [device for device, _ in calls] == ["cuda:0", "cpu"]
    assert calls[0][1] == calls[1][1]
    instance.worker.process = None


@pytest.mark.parametrize("device,error", [("auto", "invalid image path"), ("cpu", "CUDA driver failure"),
                                          ("auto", "CUDA driver failure")])
def test_local_ocr_fails_closed_without_retry_loops(device, error, monkeypatch):
    instance = local.HayaiOCRProvider({"device": device})
    instance.state = ProviderState.READY
    instance._device = "cuda:0" if device == "auto" else "cpu"
    instance.worker.actual_device = instance._device
    instance.worker.process = SimpleNamespace(poll=lambda: None)
    monkeypatch.setattr(instance, "ensure_loaded", lambda: None)
    monkeypatch.setattr(local, "resolve_assets", lambda *a, **k: (Path("/model"), None))
    attempts = []
    monkeypatch.setattr(instance.worker, "close", lambda: setattr(instance.worker, "process", None))

    def start(*args):
        instance.worker.actual_device = "cpu"

    def recognize(*args):
        attempts.append(True)
        raise RuntimeError(error)

    monkeypatch.setattr(instance.worker, "start", start)
    monkeypatch.setattr(instance.worker, "recognize", recognize)
    with pytest.raises(ProviderError, match="未覆盖原文"):
        instance.recognize(Path("/original.png"), [0, 0, 20, 20], "horizontal")
    assert len(attempts) == (2 if device == "auto" and "CUDA" in error else 1)
    assert instance.state == ProviderState.ERROR


def test_rfdetr_auto_retries_initialization_on_cpu(monkeypatch):
    instance = LayoutTextClassifier({"device": "auto"})
    starts = []

    def start():
        starts.append(instance._effective_device)
        if instance._effective_device == "auto":
            raise RuntimeError("CUDA initialization error")

    monkeypatch.setattr(instance, "_start", start)
    instance.ensure_loaded()
    assert starts == ["auto", "cpu"]
    assert instance.device == "auto"
    assert instance._fallback_reason == "accelerator_initialization_failed"
    instance.reset()
    assert instance._effective_device == "auto"


def test_rfdetr_auto_retries_inference_with_identical_request(monkeypatch):
    instance = LayoutTextClassifier({"device": "auto"})
    attempts = []

    def start():
        instance._actual_device = "cpu" if instance._effective_device == "cpu" else "cuda:0"

    def predict(request):
        attempts.append((instance._actual_device, request))
        if instance._actual_device == "cuda:0":
            raise RuntimeError("CUDNN kernel failed")
        return ["prediction"]

    monkeypatch.setattr(instance, "_start", start)
    monkeypatch.setattr(instance, "_predict", predict)
    assert instance.detect_layout(Path("original.png"), thresholds=(.25, .5, .5, .5)) == ["prediction"]
    assert [device for device, _ in attempts] == ["cuda:0", "cpu"]
    assert attempts[0][1] == attempts[1][1]
    assert instance._fallback_reason == "accelerator_inference_failed"


@pytest.mark.parametrize("error", ["model_checksum_mismatch", "runtime_not_installed"])
def test_rfdetr_non_gpu_failures_do_not_trigger_cpu_retry(error, monkeypatch):
    instance = LayoutTextClassifier({"device": "auto"})
    attempts = []

    def start():
        attempts.append(True)
        raise RuntimeError(error)

    monkeypatch.setattr(instance, "_start", start)
    with pytest.raises(ProviderError, match="原有区域保持不变"):
        instance.detect_layout(Path("original.png"))
    assert len(attempts) == 1


def test_rfdetr_cpu_retry_fails_closed_and_stops(monkeypatch):
    instance = LayoutTextClassifier({"device": "auto"})
    starts = []

    def start():
        starts.append(instance._effective_device)
        raise RuntimeError("CUDA initialization failed")

    monkeypatch.setattr(instance, "_start", start)
    with pytest.raises(ProviderError, match="原有区域保持不变"):
        instance.detect_layout(Path("original.png"))
    assert starts == ["auto", "cpu"]


def test_hybrid_top_level_auto_overrides_legacy_nested_cpu():
    instance = HybridInpainter({"device": "auto", "lama": {"device": "cpu", "other": 7}})
    assert instance._lama.config == {"device": "auto", "other": 7}
    legacy = HybridInpainter({"lama": {"device": "cpu"}})
    assert legacy._lama.config["device"] == "cpu"

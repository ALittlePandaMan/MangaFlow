"""Changing deployment mode must preserve saved GPU and cloud preferences."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.core.database import get_db
from app.core.secrets import get_secret_store
from app.main import app
from app.models import ModelConfig
from app.services.detection.classification import LayoutTextClassifier
from app.services.infra import device
from app.services.infra import model_provisioning as provisioning
from app.services.mcp_workflows import blockers
from app.services.ocr import local
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.mark.parametrize("requested", ["auto", "cuda:0", "cuda:2", "gpu", "gpu:1", "cpu"])
def test_explicit_cpu_runtime_overrides_gpu_requests_even_when_cuda_is_available(monkeypatch, requested):
    policy = SimpleNamespace(runtime="cpu")
    monkeypatch.setattr(device, "get_settings", lambda: policy)
    monkeypatch.setattr(device, "torch_cuda_available", lambda: True)
    monkeypatch.setattr(device, "paddle_cuda_available", lambda: True)
    assert device.resolve_torch_device(requested) == "cpu"
    assert device.resolve_paddle_device(requested) == "cpu"

    policy.runtime = "gpu"
    assert device.resolve_torch_device("cuda:2") == "cuda:2"
    assert device.resolve_paddle_device("cuda:2") == "gpu:2"


def test_cpu_layout_worker_reset_keeps_saved_cuda_preference(monkeypatch):
    monkeypatch.setattr(device, "get_settings", lambda: SimpleNamespace(runtime="cpu"))
    config = {"device": "cuda:0", "text_threshold": .25}
    worker = LayoutTextClassifier(config)
    assert worker.device == "cpu"
    worker.reset()
    assert worker._effective_device == "cpu"
    assert worker.options == config == {"device": "cuda:0", "text_threshold": .25}


@pytest.mark.parametrize("provider", [local.PaddleOCRVLProvider, local.BaberuOCRProvider, local.HayaiOCRProvider])
def test_cpu_ocr_worker_load_and_reset_do_not_report_a_gpu_failure(monkeypatch, provider):
    monkeypatch.setattr(device, "get_settings", lambda: SimpleNamespace(runtime="cpu"))
    monkeypatch.setattr(local, "resolve_assets", lambda *a, **k: (Path("/model"), None))
    instance = provider({"device": "cuda:0"})
    requested = []

    def start(*args):
        requested.append(instance.worker.effective_device)
        instance.worker.actual_device = "cpu"

    monkeypatch.setattr(instance.worker, "start", start)
    instance.ensure_loaded()
    assert requested == ["cpu"]
    assert instance._device == "cpu"
    assert instance._fallback_reason is None
    instance.unload()
    assert instance.worker.effective_device == "cpu"
    assert instance.config["device"] == "cuda:0"


@pytest.mark.parametrize("ocr", ["paddleocr-vl-1.6", "paddleocr", "cloud-multimodal"])
def test_gpu_cpu_gpu_switch_reopens_web_and_mcp_without_changing_models(client, monkeypatch, model_engine, ocr):
    policy = SimpleNamespace(runtime="gpu")
    monkeypatch.setattr(device, "get_settings", lambda: policy)
    monkeypatch.setattr(provisioning, "torch_cuda_available", lambda: False)
    monkeypatch.setattr(provisioning, "paddle_cuda_available", lambda: False)
    monkeypatch.setattr(provisioning, "_has_rendering_font", lambda: True)
    monkeypatch.setattr(provisioning.registry, "is_installed", lambda *args: True)
    loaded = []

    def create(kind, provider, config):
        loaded.append((kind, provider, deepcopy(config)))
        return SimpleNamespace(ensure_loaded=lambda: None)

    monkeypatch.setattr(provisioning.registry, "create", create)
    cloud_config = {"base_url": "https://example.invalid/v1", "model": "saved-model"}
    secret = get_secret_store().encrypt("test-only-saved-key")
    with Session(model_engine) as db:
        for kind, provider in (("detection", "rfdetr"), ("inpainting", "lama"), ("ocr", ocr),
                               ("rendering", "pillow"), ("translation", "openai-compatible")):
            remote = provider in {"cloud-multimodal", "openai-compatible"}
            db.add(ModelConfig(kind=kind, name=kind, provider=provider, is_default=True,
                config=deepcopy(cloud_config) if remote else {"device": "cuda:0" if kind != "rendering" else "cpu"},
                encrypted_api_key=secret if remote else None))
        db.commit()

        def saved():
            db.expire_all()
            return {item.kind: (item.id, item.provider, deepcopy(item.config), item.encrypted_api_key)
                    for item in db.scalars(select(ModelConfig))}

        before = saved()

        def database():
            yield db

        monkeypatch.setitem(app.dependency_overrides, get_db, database)
        run = SimpleNamespace(options={"translation_mode": "configured"}, workflow_id="translate_and_review")
        assert not client.get("/api/models/setup-status").json()["ready"]
        assert blockers(db, run)

        policy.runtime = "cpu"
        assert client.get("/api/models/setup-status").json()["ready"]
        verified = client.post("/api/models/setup-status/verify")
        assert verified.status_code == 200, verified.text
        assert verified.json()["ready"]
        assert blockers(db, run) == []
        assert loaded
        for kind, _, config in loaded:
            expected = deepcopy(before[kind][2])
            if before[kind][3]:
                expected["api_key"] = "test-only-saved-key"
            assert config == expected
        assert saved() == before

        # A broken GPU environment must still be reported when CPU mode ends.
        policy.runtime = "gpu"
        assert not client.get("/api/models/setup-status").json()["ready"]
        assert blockers(db, run)
        monkeypatch.setattr(provisioning, "torch_cuda_available", lambda: True)
        monkeypatch.setattr(provisioning, "paddle_cuda_available", lambda: True)
        assert client.get("/api/models/setup-status").json()["ready"]
        assert blockers(db, run) == []
        assert saved() == before

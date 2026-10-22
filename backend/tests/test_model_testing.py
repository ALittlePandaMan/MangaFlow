from copy import deepcopy
from uuid import uuid4

import httpx
import pytest
from app.core.database import SessionLocal
from app.core.secrets import get_secret_store
from app.models import ModelConfig
from app.services.base import OCRResult, ProviderError
from app.services.registry import registry
from PIL import Image
from sqlalchemy import func, select


def payload(kind="translation", provider="openai-compatible", **extra):
    return {"kind": kind, "provider": provider, "name": "unsaved-test", **extra}


def connection():
    return {"base_url": "https://example.test/v1", "model": "test-model", "api_protocol": "responses"}


def saved(kind="translation", provider="openai-compatible", config=None):
    with SessionLocal() as db:
        item = ModelConfig(kind=kind, provider=provider, name=f"test-{uuid4()}", enabled=True, is_default=False,
            config=config or connection(), encrypted_api_key=get_secret_store().encrypt("saved-secret"))
        db.add(item)
        db.commit()
        return item.id


@pytest.mark.parametrize("kind,provider", [("detection", "opencv-fallback"), ("inpainting", "opencv"), ("rendering", "pillow")])
def test_real_local_service_probe_is_disposable(client, kind, provider):
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(ModelConfig))
    response = client.post("/api/models/test", json=payload(kind, provider, config={"device": "cpu"}))
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is True, response.text
    assert response.json()["mode"] == "local"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ModelConfig)) == before


@pytest.mark.parametrize("provider", ["manga-ocr", "paddleocr-vl-1.6"])
def test_local_ocr_exercises_unsaved_settings_without_downloading(client, monkeypatch, provider):
    calls = []

    class OCR:
        capabilities = registry.ocr[provider].capabilities

        def ensure_loaded(self):
            calls.append("loaded")

        def recognize(self, path, bbox, orientation, padding):
            assert Image.open(path).size == (256, 96)
            calls.append((bbox, orientation, padding))
            return OCRResult("TEST", 1, orientation)

    def create(kind, name, config):
        assert (kind, name, config["device"]) == ("ocr", provider, "cpu")
        return OCR()

    monkeypatch.setattr(registry, "create", create)
    response = client.post("/api/models/test", json=payload("ocr", provider, config={"device": "cpu"}))
    assert response.json()["ok"] is True, response.text
    assert len(calls) == 2


@pytest.mark.parametrize("provider", ["openai-compatible", "google-web"])
def test_translation_probe_uses_only_sample_text_and_does_not_save(client, monkeypatch, provider):
    calls = []

    class Translator:
        capabilities = registry.translators[provider].capabilities

        def __init__(self, config):
            calls.append(deepcopy(config))

        async def translate_regions(self, regions, **kwargs):
            assert regions == [("TEST", "こんにちは")]
            assert kwargs == {"source_language": "ja", "target_language": "zh", "context": {}}
            return {"TEST": "你好"}

    monkeypatch.setitem(registry.translators, provider, Translator)
    config = {} if provider == "google-web" else connection()
    response = client.post("/api/models/test", json=payload(provider=provider, config=config, api_key="typed-secret"))
    assert response.json()["ok"] is True, response.text
    assert "typed-secret" not in response.text
    assert calls[0]["timeout"] == 30 and calls[0]["retries"] == 0
    if provider == "google-web":
        assert "api_key" not in calls[0]
    else:
        assert calls[0]["api_key"] == "typed-secret"
        assert calls[0]["api_protocol"] == "responses"


def mock_cloud_ocr(monkeypatch, calls):
    class OCR:
        capabilities = registry.ocr["cloud-multimodal"].capabilities

        def __init__(self, config):
            calls.append(deepcopy(config))

        def ensure_loaded(self):
            pass

        def recognize(self, path, bbox, orientation, padding):
            assert "mangaflow-model-test-" in str(path)
            return OCRResult("TEST", 1, orientation)

    monkeypatch.setitem(registry.ocr, "cloud-multimodal", OCR)


def test_cloud_ocr_uses_new_sample_and_saved_key_without_modifying_config(client, monkeypatch):
    identifier = saved("ocr", "cloud-multimodal")
    calls = []
    mock_cloud_ocr(monkeypatch, calls)
    config = {**connection(), "model": "unsaved-vision"}
    response = client.post("/api/models/test", json=payload("ocr", "cloud-multimodal", config=config, config_id=identifier))
    assert response.json()["ok"] is True, response.text
    assert calls[0]["api_key"] == "saved-secret" and calls[0]["model"] == "unsaved-vision"
    with SessionLocal() as db:
        assert db.get(ModelConfig, identifier).config["model"] == "test-model"
    assert "saved-secret" not in response.text


def test_cloud_ocr_can_test_the_default_translation_connection_without_saving_ocr(client, monkeypatch):
    identifier = saved()
    with SessionLocal() as db:
        db.get(ModelConfig, identifier).is_default = True
        db.commit()
        before = db.scalar(select(func.count()).select_from(ModelConfig))
    calls = []
    mock_cloud_ocr(monkeypatch, calls)
    response = client.post("/api/models/test", json=payload("ocr", "cloud-multimodal", reuse_translation=True, config={"timeout": 90}))
    assert response.json()["ok"] is True, response.text
    assert calls[0]["api_key"] == "saved-secret" and calls[0]["model"] == "test-model"
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ModelConfig)) == before


def test_saved_key_cannot_be_sent_to_changed_origin_or_mismatched_provider(client, monkeypatch):
    identifier = saved()
    create = []
    monkeypatch.setattr(registry, "create", lambda *args: create.append(args))
    changed = client.post("/api/models/test", json=payload(config={**connection(), "base_url": "https://other.test/v1"}, config_id=identifier))
    assert changed.status_code == 422
    mismatch = client.post("/api/models/test", json=payload("ocr", "cloud-multimodal", config=connection(), config_id=identifier))
    assert mismatch.status_code == 422
    assert create == []


@pytest.mark.parametrize("url", ["file:///etc/passwd", "https://user:password@example.test", "https://example.test/?key=secret"])
def test_probe_rejects_unsafe_connection_urls(client, url):
    response = client.post("/api/models/test", json=payload(config={**connection(), "base_url": url}, api_key="typed-secret"))
    assert response.status_code == 422
    assert "typed-secret" not in response.text


@pytest.mark.parametrize("error", [ProviderError("secret typed-secret", status_code=401), httpx.ReadTimeout("https://secret.test/?key=typed-secret"), ValueError("typed-secret")])
def test_failed_remote_probe_never_echoes_upstream_secrets(client, monkeypatch, error):
    class Translator:
        capabilities = registry.translators["openai-compatible"].capabilities

        def __init__(self, config):
            pass

        async def translate_regions(self, *args, **kwargs):
            raise error

    monkeypatch.setitem(registry.translators, "openai-compatible", Translator)
    response = client.post("/api/models/test", json=payload(config=connection(), api_key="typed-secret"))
    assert response.status_code == 200 and response.json()["ok"] is False
    assert "typed-secret" not in response.text and "secret.test" not in response.text


def test_missing_local_dependency_is_a_failed_probe_not_a_server_error(client, monkeypatch):
    def missing(*args):
        raise ProviderError("Model weights missing")

    monkeypatch.setattr(registry, "create", missing)
    response = client.post("/api/models/test", json=payload("detection", "rfdetr"))
    assert response.status_code == 200 and response.json()["ok"] is False
    assert "Model weights missing" in response.json()["message"]


def test_fallback_noop_is_not_reported_as_a_successful_service(client):
    response = client.post("/api/models/test", json=payload("translation", "passthrough"))
    assert response.status_code == 422

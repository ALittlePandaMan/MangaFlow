from __future__ import annotations

import base64
import io
import json

import httpx
import pytest
from app.core.database import Base
from app.core.secrets import get_secret_store
from app.models import ModelConfig
from app.services.base import ProviderError
from app.services.infra.model_provisioning import setup_status_report
from app.services.ocr import generic
from app.services.ocr.generic import GenericCloudOCRProvider
from app.services.ocr.prompts import DEFAULT_OCR_PROMPT
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

CONFIG = {"base_url": "https://ocr.example/recognize", "api_key": "ocr-test-key", "retries": 0}


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (160, 240), "white").save(path)
    return path


def mock_client(monkeypatch, handler):
    real = httpx.Client
    monkeypatch.setattr(generic.httpx, "Client", lambda **kwargs: real(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(generic.time, "sleep", lambda _: None)


@pytest.mark.parametrize("format", ["json", "multipart", "chat_completions"])
def test_only_crop_uploaded_and_plain_text_returned(source, monkeypatch, format):
    requests = []
    def handler(request):
        requests.append(request)
        body = {"text": "原文\n二行"} if format != "chat_completions" else {"choices": [{"finish_reason": "stop", "message": {"content": "原文\n二行"}}]}
        return httpx.Response(200, json=body)
    mock_client(monkeypatch, handler)
    provider = GenericCloudOCRProvider({**CONFIG, "request_format": format, "model": "PaddlePaddle/PaddleOCR-VL-1.5", "extra_fields": {"language": "ja"}})
    provider.ensure_loaded()
    assert not requests
    result = provider.recognize(source, [10, 20, 40, 60], "vertical")
    assert result.text == "原文\n二行" and not result.metadata["confidence_available"]
    request = requests[0]
    assert str(request.url) == CONFIG["base_url"]
    assert request.headers["authorization"] == "Bearer ocr-test-key"
    if format == "multipart":
        assert b'filename="region.png"' in request.content
        assert b'image/png' in request.content
    else:
        body = json.loads(request.content)
        encoded = body["image"] if format == "json" else body["messages"][0]["content"][0]["image_url"]["url"].split(",")[1]
        assert Image.open(io.BytesIO(base64.b64decode(encoded))).size == (48, 68)
        if format == "chat_completions":
            assert body["messages"][0]["content"][1]["text"] == "OCR:"
            assert body["max_tokens"] == 2048 and body["stream"] is False


@pytest.mark.parametrize("body,path,expected", [
    ({"text": ""}, "text", ""),
    ({"data": {"words": [{"text": "甲"}, {"text": "乙"}]}}, "data.words.*.text", "甲\n乙"),
    ({"result": ["一", "二"]}, "result.*", "一\n二"),
    ({"result": []}, "result.*", ""),
    ({"result": [{"text": "甲"}]}, "result.0.text", "甲"),
])
def test_field_mapping(body, path, expected):
    assert GenericCloudOCRProvider.extract_text(body, path) == expected


@pytest.mark.parametrize("body", [{"error": "no text"}, {"text": None}, {"text": {"x": 1}}, {"text": ["a"]}])
def test_invalid_mapping_rejected(body):
    with pytest.raises((ValueError, TypeError, KeyError)):
        GenericCloudOCRProvider.extract_text(body, "text")


@pytest.mark.parametrize("status,count", [(401, 1), (302, 1), (429, 3), (500, 3)])
def test_no_redirect_and_sanitized_retries(source, monkeypatch, status, count):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="secret upstream ocr-test-key", headers={"location": "https://other.example"})
    mock_client(monkeypatch, handler)
    with pytest.raises(ProviderError) as exc:
        GenericCloudOCRProvider({**CONFIG, "retries": 2}).recognize(source, [0, 0, 20, 20], "horizontal")
    assert len(requests) == count
    assert "ocr-test-key" not in str(exc.value) and "upstream" not in str(exc.value)


@pytest.mark.parametrize("mode", ["api_key", "none"])
def test_auth_modes_and_data_url(source, monkeypatch, mode):
    def handler(request):
        assert "authorization" not in request.headers
        assert request.headers.get("x-ocr-key") == ("ocr-test-key" if mode == "api_key" else None)
        assert json.loads(request.content)["file"].startswith("data:image/png;base64,")
        return httpx.Response(200, json={"text": "原文"})
    mock_client(monkeypatch, handler)
    GenericCloudOCRProvider({**CONFIG, "auth_mode": mode, "api_key_header": "X-OCR-Key", "image_encoding": "data_url", "image_field": "file"}).recognize(source, [0, 0, 20, 20], "horizontal")


@pytest.mark.parametrize("patch", [
    {"base_url": "file:///x"}, {"base_url": "https://user:password@host/x"},
    {"base_url": "https://host/x?key=bad"}, {"auth_mode": "invalid"}, {"timeout": -1}, {"retries": 1.5},
    {"extra_fields": {"nested": {"api_key": "secret"}}}, {"extra_fields": {"image": "override"}},
    {"api_key_header": "Host"}, {"api_key_header": "X-Key\r\nHost"}, {"response_text_path": "a..b"},
    {"request_format": "chat_completions"}, {"request_format": "chat_completions", "model": "ocr", "extra_fields": {"stream": True}},
])
def test_config_validation(patch):
    with pytest.raises(ProviderError):
        GenericCloudOCRProvider.validate_config({**CONFIG, **patch})


def test_truncation_and_refusal_rejected(source, monkeypatch):
    for choice in [{"finish_reason": "length", "message": {"content": "partial"}}, {"message": {"content": "", "refusal": "no"}}]:
        mock_client(monkeypatch, lambda request, choice=choice: httpx.Response(200, json={"choices": [choice]}))
        with pytest.raises(ProviderError):
            GenericCloudOCRProvider({**CONFIG, "request_format": "chat_completions", "model": "ocr"}).recognize(source, [0, 0, 20, 20], "vertical")
        monkeypatch.undo()


def test_generic_setup_without_key_when_auth_disabled():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(ModelConfig(kind="ocr", name="generic", provider="cloud-ocr", enabled=True, is_default=True, config={"base_url": CONFIG["base_url"], "auth_mode": "none"}))
        db.commit()
        assert next(item for item in setup_status_report(db)["stages"] if item["kind"] == "ocr")["status"] == "ready"
    engine.dispose()


def test_reuse_translation_copies_only_connection_and_never_leaks_key(client):
    translation = {"kind": "translation", "provider": "openai-compatible", "name": "reuse-test-translation", "is_default": True,
                   "api_key": "private-translation-key", "config": {"base_url": "https://translation.example/v1", "api_protocol": "responses", "model": "gemini-3.8-flash-high", "prompt": "Translate everything", "temperature": 0.8}}
    saved = client.post("/api/models/config", json=translation)
    assert saved.status_code == 201
    payload = {"kind": "ocr", "provider": "cloud-multimodal", "name": "reuse-test-ocr", "reuse_translation": True,
               "config": {"prompt": DEFAULT_OCR_PROMPT, "base_url": "https://evil.example/v1", "model": "ignored"}}
    response = client.post("/api/models/config", json=payload)
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["has_api_key"] and "private-translation-key" not in response.text
    assert data["config"]["base_url"] == "https://translation.example/v1"
    assert data["config"]["model"] == "gemini-3.8-flash-high"
    assert data["config"]["prompt"] == DEFAULT_OCR_PROMPT
    assert data["config"]["api_protocol"] == "responses" and "temperature" not in data["config"]
    assert client.get("/api/models").json()["configured"]
    from app.core.database import SessionLocal
    with SessionLocal() as db:
        ocr = db.get(ModelConfig, data["id"])
        assert get_secret_store().decrypt(ocr.encrypted_api_key) == "private-translation-key"
    assert client.post("/api/models/config", json={**payload, "provider": "cloud-ocr"}).status_code == 422
    client.delete(f"/api/models/config/{saved.json()['id']}")
    assert client.post("/api/models/config", json={**payload, "name": "missing-translation"}).status_code == 422
    client.delete(f"/api/models/config/{data['id']}")

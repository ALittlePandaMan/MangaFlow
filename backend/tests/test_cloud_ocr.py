from __future__ import annotations

import base64
import io
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from app.api import models as model_api
from app.core.database import Base
from app.models import ModelConfig
from app.services.base import ProviderError
from app.services.infra import model_manifest as manifests
from app.services.infra.model_provisioning import setup_status_report
from app.services.ocr import cloud
from app.services.ocr.cloud import CloudMultimodalOCRProvider
from app.services.ocr.prompts import DEFAULT_OCR_PROMPT
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from test_model_manifest import manifest_payload
from test_pipeline import manga_page, wait_for_task

CONFIG = {"base_url": "https://vision.example/v1", "model": "vision-model", "api_key": "private-key", "retries": 0}


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "page.png"
    Image.new("RGB", (160, 240), "white").save(path)
    return path


def response_body(protocol, text="こんにちは！"):
    content = json.dumps({"text": text}, ensure_ascii=False)
    if protocol == "anthropic":
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": content}]}
    if protocol == "responses":
        return {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": content}]}]}
    return {"choices": [{"finish_reason": "stop", "message": {"content": content}}]}


def mock_client(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(cloud.httpx, "Client", lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setattr(cloud.time, "sleep", lambda _: None)


@pytest.mark.parametrize("protocol,endpoint", [("openai", "chat/completions"), ("responses", "responses"), ("anthropic", "messages")])
def test_protocol_uploads_only_crop_and_honors_prompt(monkeypatch, source, protocol, endpoint):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json=response_body(protocol))
    mock_client(monkeypatch, handler)
    provider = CloudMultimodalOCRProvider({**CONFIG, "api_protocol": protocol, "prompt": "Read original Japanese carefully."})
    provider.ensure_loaded()
    assert requests == []
    result = provider.recognize(source, [10, 20, 40, 60], "vertical", 4)
    assert result.text == "こんにちは！"
    assert result.orientation == "vertical"
    assert result.metadata["confidence_available"] is False
    request = requests[0]
    assert str(request.url) == f"https://vision.example/v1/{endpoint}"
    body = json.loads(request.content)
    assert body["model"] == "vision-model"
    assert "temperature" not in body
    assert "Read original Japanese carefully." in str(body)
    assert "vertical" in str(body)
    if protocol == "anthropic":
        assert request.headers["x-api-key"] == "private-key"
        encoded = body["messages"][0]["content"][0]["source"]["data"]
    else:
        assert request.headers["authorization"] == "Bearer private-key"
        image = body["input"][0]["content"][0]["image_url"] if protocol == "responses" else body["messages"][1]["content"][0]["image_url"]["url"]
        encoded = image.split(",")[1]
    assert Image.open(io.BytesIO(base64.b64decode(encoded))).size == (48, 68)


@pytest.mark.parametrize("status,retry_count", [(401, 1), (400, 1), (302, 1), (429, 3), (503, 3)])
def test_retry_policy_and_secret_safe_errors(monkeypatch, source, status, retry_count):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(status, text="private-key secret upstream body", headers={"location": "https://other.example"})
    mock_client(monkeypatch, handler)
    with pytest.raises(ProviderError) as error:
        CloudMultimodalOCRProvider({**CONFIG, "retries": 2}).recognize(source, [0, 0, 10, 10], "horizontal")
    assert len(requests) == retry_count
    assert "private-key" not in str(error.value)
    assert "upstream" not in str(error.value)


@pytest.mark.parametrize("protocol,stop", [("openai", "length"), ("responses", "incomplete"), ("anthropic", "max_tokens")])
def test_truncated_response_is_not_accepted(protocol, stop):
    body = response_body(protocol)
    if protocol == "openai":
        body["choices"][0]["finish_reason"] = stop
    else:
        body["status" if protocol == "responses" else "stop_reason"] = stop
    with pytest.raises(ProviderError, match="截断"):
        CloudMultimodalOCRProvider._parse_response(body, protocol)


@pytest.mark.parametrize("text", ["", "日本語\n原文！"])
def test_empty_and_multiline_transcription(text):
    assert CloudMultimodalOCRProvider._parse_response(response_body("openai", text), "openai") == text


def test_malformed_response_and_refusal_do_not_become_source(monkeypatch, source):
    mock_client(monkeypatch, lambda request: httpx.Response(200, json={"choices": [{"message": {"content": "Here is the text"}}]}))
    with pytest.raises(ProviderError, match="JSON"):
        CloudMultimodalOCRProvider(CONFIG).recognize(source, [0, 0, 10, 10], "vertical")
    with pytest.raises(ProviderError, match="拒绝"):
        CloudMultimodalOCRProvider._parse_response({"choices": [{"message": {"refusal": "no", "content": None}}]}, "openai")


@pytest.mark.parametrize("patch", [{"model": ""}, {"base_url": "file:///a"}, {"base_url": "https://key@host/v1"}, {"retries": 1.5}, {"temperature": 2}, {"timeout": 0}, {"max_tokens": -1}, {"api_protocol": "bad"}])
def test_invalid_config(patch):
    with pytest.raises(ProviderError):
        CloudMultimodalOCRProvider.validate_config({**CONFIG, **patch})


def test_cloud_ocr_config_roundtrip_and_independent_key(client, monkeypatch, tmp_path):
    monkeypatch.setattr(model_api, "get_settings", lambda: SimpleNamespace(model_manifest_path=tmp_path / "config.yaml", environment_file_path=tmp_path / ".env"))
    client.post("/api/models/bootstrap", json={"preload": False})
    payload = {"kind": "ocr", "name": "test-cloud-ocr", "provider": "cloud-multimodal", "enabled": True, "is_default": True,
               "config": {key: value for key, value in CONFIG.items() if key != "api_key"}, "api_key": "separate-ocr-secret"}
    payload["config"]["prompt"] = DEFAULT_OCR_PROMPT
    response = client.post("/api/models/config", json=payload)
    assert response.status_code == 201, response.text
    assert response.json()["has_api_key"]
    assert "separate-ocr-secret" not in response.text
    config_id = response.json()["id"]
    del payload["api_key"]
    response = client.patch(f"/api/models/config/{config_id}", json=payload)
    assert response.status_code == 200 and response.json()["has_api_key"]
    manifest = manifests.load_model_manifest(tmp_path / "config.yaml")
    assert manifest.stages["ocr"].api_key_env == "MANGAFLOW_OCR_API_KEY"
    assert manifest.stages["ocr"].device is None
    assert "separate-ocr-secret" not in (tmp_path / "config.yaml").read_text()
    assert 'MANGAFLOW_OCR_API_KEY="separate-ocr-secret"' in (tmp_path / ".env").read_text()
    changed = deepcopy(payload)
    changed["config"]["base_url"] = "https://different.example/v1"
    assert client.patch(f"/api/models/config/{config_id}", json=changed).status_code == 422
    assert client.post("/api/models/discover", json={"base_url": "https://different.example/v1", "config_id": config_id}).status_code == 422
    local = {**payload, "provider": "review-fallback", "config": {}, "name": "test-local-ocr"}
    response = client.patch(f"/api/models/config/{config_id}", json=local)
    assert response.status_code == 200 and not response.json()["has_api_key"]
    assert manifests.load_model_manifest(tmp_path / "config.yaml").stages["ocr"].api_key_env is None


def test_manifest_cloud_ocr_reload_and_setup_never_call_network(monkeypatch):
    monkeypatch.setattr(cloud.httpx, "Client", lambda **kwargs: pytest.fail("Setup must not upload images"))
    monkeypatch.setenv("MANGAFLOW_OCR_API_KEY", "ocr-environment-key")
    monkeypatch.setenv("MANGAFLOW_TRANSLATION_API_KEY", "translation-environment-key")
    payload = manifest_payload(lightweight=True)
    payload["stages"]["ocr"] = {"provider": "cloud-multimodal", "api_key_env": "MANGAFLOW_OCR_API_KEY", "config": {"base_url": CONFIG["base_url"], "model": "vision-model"}}
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        manifests.apply_model_manifest(db, payload)
        stage = next(item for item in setup_status_report(db, validate_models=True)["stages"] if item["kind"] == "ocr")
        assert stage["status"] == "ready"
        report = manifests.preload_manifest_models(db, payload)
        assert next(item for item in report if item["kind"] == "ocr")["status"] == "skipped"
        ocr = db.scalar(select(ModelConfig).where(ModelConfig.kind == "ocr"))
        ocr.encrypted_api_key = None
        db.commit()
        assert next(item for item in setup_status_report(db)["stages"] if item["kind"] == "ocr")["status"] == "error"
    engine.dispose()


def test_pipeline_can_rerun_selected_cloud_provider_and_preserve_source_on_failure(client, monkeypatch):
    provider_config = {"kind": "ocr", "name": "pipeline-cloud-ocr", "provider": "cloud-multimodal", "enabled": True, "is_default": False,
                       "api_key": "pipeline-test-key", "config": {key: value for key, value in CONFIG.items() if key != "api_key"}}
    saved = client.post("/api/models/config", json=provider_config)
    assert saved.status_code == 201, saved.text
    project = client.post("/api/projects", json={"name": "Cloud OCR pipeline test"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("page.png", manga_page(), "image/png")}).json()[0]
    region = client.post(f"/api/images/{page['id']}/regions", json={"bbox": [100, 100, 80, 120]}).json()
    client.patch(f"/api/regions/{region['id']}", json={"source_text": "previous text"})
    responses = [httpx.Response(200, json=response_body("openai", "新しい原文")), httpx.Response(401, text="private upstream")]
    requests = []
    def handler(request):
        requests.append(request)
        return responses.pop(0)
    mock_client(monkeypatch, handler)
    for expected in ["COMPLETED", "FAILED"]:
        started = client.post(f"/api/images/{page['id']}/process", json={"start_stage": "ocr", "end_stage": "ocr", "force": True, "provider": "pipeline-cloud-ocr"}).json()
        task = wait_for_task(client, started["id"])
        assert task["status"] == expected, task
        updated = client.get(f"/api/images/{page['id']}/regions").json()[0]
        assert updated["source_text"] == "新しい原文"
        assert updated["bbox"] == region["bbox"]
        assert "low_ocr_confidence" not in updated["review_reasons"]
        assert updated["layout_data"]["ocr"]["provider"] == "cloud-multimodal"
    assert len(requests) == 2
    assert requests[0].headers["authorization"] == "Bearer pipeline-test-key"
    client.delete(f"/api/models/config/{saved.json()['id']}")

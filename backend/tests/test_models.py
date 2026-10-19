from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml
from app.api import models as model_api
from app.api.models import _extract_model_ids, _model_endpoint_candidates
from app.core.database import Base, get_db
from app.main import app
from app.services.infra.paddle import extract_paddle_lines
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def fresh_model_database(monkeypatch, tmp_path):
    # First-run defaults must not inherit deliberate CPU/custom selections
    # from earlier integration tests sharing the main test database.
    engine = create_engine(f"sqlite:///{tmp_path / 'models.db'}")
    Base.metadata.create_all(engine)

    def database():
        with Session(engine) as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, database)
    try:
        yield
    finally:
        engine.dispose()


class FakePaddleResult:
    json = {
        "res": {
            "rec_polys": [[[10, 20], [40, 20], [40, 80], [10, 80]]],
            "rec_texts": ["こんにちは"],
            "rec_scores": [0.93],
        }
    }


def test_extract_paddle_3_result() -> None:
    assert extract_paddle_lines([FakePaddleResult()]) == [
        {
            "polygon": [[10, 20], [40, 20], [40, 80], [10, 80]],
            "text": "こんにちは",
            "confidence": 0.93,
        }
    ]


def test_extract_openai_and_ollama_model_lists() -> None:
    assert _extract_model_ids({"data": [{"id": "model-a"}, {"id": "model-b"}, {"id": "model-a"}]}) == [
        "model-a",
        "model-b",
    ]
    assert _extract_model_ids({"models": [{"name": "qwen"}, {"model": "llama"}]}) == ["qwen", "llama"]


def test_model_discovery_tries_v1_for_root_base_url() -> None:
    assert _model_endpoint_candidates("http://example.com:9000") == [
        "http://example.com:9000/models",
        "http://example.com:9000/v1/models",
    ]
    assert _model_endpoint_candidates("http://example.com:9000/v1") == ["http://example.com:9000/v1/models"]


@pytest.mark.usefixtures("fresh_model_database")
def test_bootstrap_recommended_configs_without_downloading(client) -> None:
    response = client.post(
        "/api/models/bootstrap",
        json={"preload": False, "upgrade_fallbacks": True},
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["ok"] is True
    assert {item["kind"] for item in report["models"]} == {
        "detection",
        "ocr",
        "translation",
        "inpainting",
        "rendering",
    }
    configured = client.get("/api/models").json()["configured"]
    defaults = {item["kind"]: item["provider"] for item in configured if item["is_default"]}
    assert defaults["detection"] == "rfdetr"
    assert defaults["ocr"] == "paddleocr-vl-1.6"
    assert defaults["inpainting"] == "lama"
    for item in configured:
        if item["is_default"] and item["kind"] in {"detection", "ocr", "inpainting"}:
            assert item["config"]["device"] == "auto"
    recommended_detection = next(
        item for item in configured if item["kind"] == "detection" and item["is_default"]
    )
    assert recommended_detection["config"]["containment_threshold"] == .9
    assert recommended_detection["config"]["text_box_padding"] == {"top": 3, "bottom": 3, "left": 3, "right": 3}
    cloud_translation = next(
        item
        for item in configured
        if item["provider"] == "openai-compatible" and item["name"] == "自定义云端翻译"
    )
    assert cloud_translation["name"] == "自定义云端翻译"
    assert "base_url" not in cloud_translation["config"]
    assert "model" not in cloud_translation["config"]
    assert "Region ID" in cloud_translation["config"]["prompt"]


def test_bootstrap_applies_user_selected_providers_and_devices(client) -> None:
    response = client.post(
        "/api/models/bootstrap",
        json={
            "preload": False,
            "selections": [
                {"kind": "detection", "provider": "opencv-fallback", "device": "cpu"},
                {"kind": "inpainting", "provider": "lama", "device": "cpu"},
                {"kind": "ocr", "provider": "manga-ocr", "device": "cpu"},
                {"kind": "rendering", "provider": "pillow"},
                {
                    "kind": "translation",
                    "provider": "openai-compatible",
                    "config": {
                        "api_protocol": "openai",
                        "base_url": "https://translation.example.com/v1",
                        "model": "manga-translator",
                    },
                    "api_key": "test-secret",
                },
            ],
        },
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["ok"] is True
    assert len(report["models"]) == 5
    assert next(item for item in report["models"] if item["kind"] == "translation")["status"] == "configured"

    configured = client.get("/api/models").json()["configured"]
    defaults = {item["kind"]: item for item in configured if item["is_default"]}
    assert defaults["detection"]["provider"] == "opencv-fallback"
    assert defaults["inpainting"]["provider"] == "lama"
    assert defaults["inpainting"]["config"]["device"] == "cpu"
    assert defaults["ocr"]["provider"] == "manga-ocr"
    assert defaults["ocr"]["config"]["device"] == "cpu"
    assert defaults["translation"]["has_api_key"] is True
    assert defaults["translation"]["config"]["base_url"] == "https://translation.example.com/v1"
    setup = client.get("/api/models/setup-status").json()
    translation = next(item for item in setup["stages"] if item["kind"] == "translation")
    assert translation["status"] == "ready"


def test_bootstrap_accepts_auto_device_for_all_local_gpu_providers(client):
    response = client.post("/api/models/bootstrap", json={"preload": False, "selections": [
        {"kind": "detection", "provider": "rfdetr", "device": "auto"},
        {"kind": "inpainting", "provider": "hybrid", "device": "auto"},
        {"kind": "ocr", "provider": "paddleocr-vl-1.6", "device": "auto"},
    ]})
    assert response.status_code == 200, response.text
    defaults = {item["kind"]: item for item in client.get("/api/models").json()["configured"] if item["is_default"]}
    for kind in ("detection", "inpainting", "ocr"):
        assert defaults[kind]["config"]["device"] == "auto"


def test_model_api_saves_active_settings_to_yaml_and_env(client, monkeypatch, tmp_path) -> None:
    manifest_path = tmp_path / "config.yaml"
    environment_path = tmp_path / ".env"
    monkeypatch.setattr(
        model_api,
        "get_settings",
        lambda: SimpleNamespace(
            model_manifest_path=manifest_path,
            environment_file_path=environment_path,
        ),
    )
    bootstrapped = client.post(
        "/api/models/bootstrap",
        json={"preload": False, "upgrade_fallbacks": True},
    )
    assert bootstrapped.status_code == 200, bootstrapped.text

    saved = client.post(
        "/api/models/config",
        json={
            "kind": "translation",
            "name": "persisted-cloud-translation",
            "provider": "openai-compatible",
            "enabled": True,
            "is_default": True,
            "config": {
                "api_protocol": "openai",
                "base_url": "https://persisted.example.com/v1",
                "model": "persisted-model",
                "temperature": 0.15,
            },
            "api_key": "persisted-secret",
        },
    )
    assert saved.status_code == 201, saved.text

    payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    translation = payload["stages"]["translation"]
    assert translation["config"]["base_url"] == "https://persisted.example.com/v1"
    assert translation["config"]["model"] == "persisted-model"
    assert translation["config"]["temperature"] == 0.15
    assert translation["api_key_env"] == "MANGAFLOW_TRANSLATION_API_KEY"
    assert "persisted-secret" not in manifest_path.read_text(encoding="utf-8")
    assert 'MANGAFLOW_TRANSLATION_API_KEY="persisted-secret"' in environment_path.read_text(encoding="utf-8")


def test_detection_padding_persists_and_invalid_updates_keep_saved_value(client, monkeypatch, tmp_path):
    manifest_path = tmp_path / "config.yaml"
    monkeypatch.setattr(model_api, "get_settings", lambda: SimpleNamespace(
        model_manifest_path=manifest_path, environment_file_path=tmp_path / ".env",
    ))
    assert client.post("/api/models/bootstrap", json={"preload": False}).status_code == 200
    payload = {"kind": "detection", "provider": "rfdetr", "name": "padding-test", "is_default": True,
               "config": {"device": "cpu", "text_box_padding": 8}}
    response = client.post("/api/models/config", json=payload)
    assert response.status_code == 201, response.text
    identifier = response.json()["id"]
    assert yaml.safe_load(manifest_path.read_text())["stages"]["detection"]["config"]["text_box_padding"] == 8
    for padding in (0, 50):
        payload["config"]["text_box_padding"] = padding
        response = client.patch(f"/api/models/config/{identifier}", json=payload)
        assert response.status_code == 200, response.text
        listed = next(c for c in client.get("/api/models").json()["configured"] if c["id"] == identifier)
        assert listed["config"]["text_box_padding"] == padding
        assert yaml.safe_load(manifest_path.read_text())["stages"]["detection"]["config"]["text_box_padding"] == padding
    payload["config"]["text_box_padding"] = -1
    assert client.patch(f"/api/models/config/{identifier}", json=payload).status_code == 422
    listed = next(c for c in client.get("/api/models").json()["configured"] if c["id"] == identifier)
    assert listed["config"]["text_box_padding"] == 50
    margins = {"top": 0, "bottom": 5, "left": 10, "right": 50}
    payload["config"]["text_box_padding"] = margins
    assert client.patch(f"/api/models/config/{identifier}", json=payload).status_code == 200
    listed = next(c for c in client.get("/api/models").json()["configured"] if c["id"] == identifier)
    assert listed["config"]["text_box_padding"] == margins
    assert yaml.safe_load(manifest_path.read_text())["stages"]["detection"]["config"]["text_box_padding"] == margins


@pytest.mark.parametrize("padding", [-1, 51, "invalid", None, {"top": -1}, {"right": 51}, {"left": None}, {"typo": 3}])
def test_detection_api_rejects_invalid_padding(client, padding):
    response = client.post("/api/models/config", json={"kind": "detection", "provider": "rfdetr",
        "name": "invalid-padding", "config": {"text_box_padding": padding}})
    assert response.status_code == 422, response.text

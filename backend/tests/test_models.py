from __future__ import annotations

from app.api.models import _extract_model_ids, _model_endpoint_candidates
from app.services.paddle import extract_paddle_lines


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
    assert _model_endpoint_candidates("http://example.com:8317") == [
        "http://example.com:8317/models",
        "http://example.com:8317/v1/models",
    ]
    assert _model_endpoint_candidates("http://example.com:8317/v1") == ["http://example.com:8317/v1/models"]


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
    assert defaults["detection"] == "paddleocr"
    assert defaults["ocr"] == "manga-ocr"
    assert defaults["inpainting"] == "lama"
    cloud_translation = next(item for item in configured if item["provider"] == "openai-compatible")
    assert cloud_translation["name"] == "自定义云端翻译"
    assert "base_url" not in cloud_translation["config"]
    assert "model" not in cloud_translation["config"]
    assert "Region ID" in cloud_translation["config"]["prompt"]

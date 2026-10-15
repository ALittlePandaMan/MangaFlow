from __future__ import annotations

import json
import time
from io import BytesIO
from unittest.mock import Mock

from app.services.base import DetectionResult
from app.services.detection.text_roles import partition_text_roles
from app.utils.geometry import bbox_to_polygon
from PIL import Image

POLYGON = bbox_to_polygon([15, 20, 20, 30])


def test_high_confidence_sfx_is_excluded_but_kept_in_report():
    value = {"label": "onomatopoeia", "status": "classified", "excluded": True}
    region = DetectionResult(POLYGON, [15, 20, 20, 30], 0.9, metadata={"text_classification": value})
    kept, report = partition_text_roles([region])
    assert kept == []
    assert report["excluded_count"] == 1
    assert report["excluded_candidates"][0]["polygon"] == POLYGON
    json.dumps(report, allow_nan=False)


def test_classification_retry_resets_only_selected_detector_without_page_mutation(client, monkeypatch):
    from threading import RLock
    from types import SimpleNamespace

    from app.core.database import SessionLocal
    from app.models import ModelConfig
    from app.services.registry import registry

    reset = Mock()
    fake = SimpleNamespace(_load_lock=RLock(), _text_classifier=SimpleNamespace(reset=reset))
    monkeypatch.setattr(registry, "create", lambda *_: fake)
    with SessionLocal() as db:
        item = ModelConfig(kind="detection", name="retry-test", provider="rfdetr", config={})
        other = ModelConfig(kind="ocr", name="unsupported-retry", provider="review-fallback", config={})
        db.add_all([item, other])
        db.commit()
        item_id, other_id = item.id, other.id
    assert client.post(f"/api/models/config/{item_id}/classification/retry").json()["status"] == "reset"
    reset.assert_called_once()
    assert client.post(f"/api/models/config/{other_id}/classification/retry").status_code == 422
    assert client.post("/api/models/config/missing/classification/retry").status_code == 404


def test_pipeline_does_not_create_regions_for_filtered_sfx(client, monkeypatch):
    from app.core.database import SessionLocal
    from app.models import ImagePage
    from app.pipeline.processor import PipelineProcessor
    from app.services.detection.providers import OpenCVTextDetector

    normal = DetectionResult(
        bbox_to_polygon([10, 10, 20, 30]),
        [10, 10, 20, 30],
        0.9,
        metadata={"text_classification": {"label": "text", "status": "classified", "excluded": False}},
    )
    sfx = DetectionResult(
        bbox_to_polygon([60, 10, 20, 30]),
        [60, 10, 20, 30],
        0.9,
        metadata={"text_classification": {"label": "onomatopoeia", "status": "classified", "excluded": True}},
    )
    monkeypatch.setattr(OpenCVTextDetector, "detect", lambda self, path: [normal, sfx])
    original_provider = PipelineProcessor._provider
    monkeypatch.setattr(
        PipelineProcessor,
        "_provider",
        lambda self, kind, requested: (
            (OpenCVTextDetector(), "classification-test")
            if kind == "detection"
            else original_provider(self, kind, requested)
        ),
    )
    project = client.post("/api/projects", json={"name": "classification"}).json()
    output = BytesIO()
    Image.new("RGB", (120, 100), "white").save(output, "PNG")
    output.seek(0)
    page = client.post(
        f"/api/projects/{project['id']}/images", files={"files": ("page.png", output, "image/png")}
    ).json()[0]
    task = client.post(
        f"/api/images/{page['id']}/process", json={"start_stage": "detection", "end_stage": "detection", "force": True}
    ).json()
    for _ in range(300):
        status = client.get(f"/api/tasks/{task['id']}").json()["status"]
        if status in {"COMPLETED", "FAILED"}:
            break
        time.sleep(0.03)
    assert status == "COMPLETED"
    regions = client.get(f"/api/images/{page['id']}/regions").json()
    assert len(regions) == 1
    assert regions[0]["bbox"] == normal.bbox
    with SessionLocal() as db:
        assert db.get(ImagePage, page["id"]).metadata_json["text_classification"]["excluded_count"] == 1
    summary = client.get(f"/api/images/{page['id']}").json()["text_classification"]
    assert summary["excluded_count"] == 1
    assert "excluded_candidates" not in summary

    def redetect():
        task = client.post(
            f"/api/images/{page['id']}/process",
            json={"start_stage": "detection", "end_stage": "detection", "force": True},
        ).json()
        for _ in range(300):
            result = client.get(f"/api/tasks/{task['id']}").json()
            if result["status"] in {"COMPLETED", "FAILED"}:
                break
            time.sleep(0.03)
        assert result["status"] == "COMPLETED"
        return client.get(f"/api/images/{page['id']}/regions").json()

    # Keep mode recovers the original SFX geometry, not a fabricated box.
    sfx.metadata["text_classification"]["excluded"] = False
    recovered = redetect()
    assert len(recovered) == 2
    assert sfx.bbox in [region["bbox"] for region in recovered]

    # User-authored boxes are never removed just because a classifier would
    # reject the same position on subsequent automatic detection.
    manual = client.post(
        f"/api/images/{page['id']}/regions", json={"polygon": sfx.polygon, "source_text": "手工原文", "locked": True}
    ).json()
    sfx.metadata["text_classification"]["excluded"] = True
    preserved = redetect()
    assert any(region["id"] == manual["id"] and region["source_text"] == "手工原文" for region in preserved)

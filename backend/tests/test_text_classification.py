from __future__ import annotations

import json
import time
from io import BytesIO
from unittest.mock import Mock

import numpy as np
import pytest
from app.services.base import DetectionResult
from app.services.detection.classification import LayoutTextClassifier
from app.services.detection.text_roles import (
    LayoutPrediction,
    classification_options,
    classify_text_polygons,
    partition_text_roles,
)
from app.utils.geometry import bbox_to_polygon
from PIL import Image


def prediction(label="onomatopoeia", confidence=0.9, box=None, ink=True):
    box = box or [10, 10, 40, 50]
    return LayoutPrediction(
        label,
        confidence,
        list(box),
        np.full((int(box[3]), int(box[2])), int(ink), np.uint8),
        (int(box[0]), int(box[1])),
    )


POLYGON = bbox_to_polygon([15, 20, 20, 30])


def test_high_confidence_sfx_is_excluded_but_kept_in_report():
    value = classify_text_polygons([POLYGON], [prediction()])[0]
    region = DetectionResult(POLYGON, [15, 20, 20, 30], 0.9, metadata={"text_classification": value})
    kept, report = partition_text_roles([region])
    assert kept == []
    assert report["excluded_count"] == 1
    assert report["excluded_candidates"][0]["polygon"] == POLYGON
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("case", ["low_confidence", "no_ink", "partial_overlap", "no_predictions", "normal_conflict"])
def test_uncertain_results_never_drop_text(case):
    values = {
        "low_confidence": [prediction(confidence=0.59)],
        "no_ink": [prediction(ink=False)],
        "partial_overlap": [prediction(box=[30, 10, 40, 50])],
        "no_predictions": [],
        "normal_conflict": [prediction(), prediction(label="text", confidence=0.4)],
    }
    result = classify_text_polygons([POLYGON], values[case])[0]
    assert result["excluded"] is False
    assert result["label"] == "uncertain"


def test_outside_caption_is_normal_text_not_sfx():
    result = classify_text_polygons([POLYGON], [prediction(label="text", confidence=0.85)])[0]
    assert result["label"] == "text"
    assert result["excluded"] is False


@pytest.mark.parametrize("protection", ["layout_bubble", "existing_bubble"])
def test_dialogue_inside_reliable_bubble_is_protected(protection):
    predictions = [prediction()]
    protected = None
    if protection == "layout_bubble":
        predictions.append(prediction(label="bubble", confidence=0.95))
    else:
        protected = {0}
    result = classify_text_polygons([POLYGON], predictions, protected_indices=protected)[0]
    assert result["label"] == "onomatopoeia"
    assert result["excluded"] is False
    assert result["reason"] == "bubble_dialogue_protection"


def test_keep_mode_keeps_classification_and_allows_recovery():
    result = classify_text_polygons([POLYGON], [prediction()], filter_sfx=False)[0]
    assert result["label"] == "onomatopoeia"
    assert result["excluded"] is False


@pytest.mark.parametrize("polygon", [[], [[1, 2], [1, 2], [1, 2]], [[float("nan"), 2], [3, 4], [4, 2]]])
def test_invalid_geometry_is_uncertain(polygon):
    result = classify_text_polygons([polygon], [prediction()])[0]
    assert not result["excluded"]


@pytest.mark.parametrize("threshold", [0, 1, float("nan")])
def test_invalid_threshold_rejected(threshold):
    with pytest.raises(ValueError):
        classify_text_polygons([POLYGON], [prediction()], min_sfx_confidence=threshold)


def test_disable_and_missing_runtime_never_remove_boxes(tmp_path):
    assert classification_options({"text_classification": False})["enabled"] is False
    region = DetectionResult(POLYGON, [15, 20, 20, 30], 0.9)
    disabled = LayoutTextClassifier({"text_classification": False})
    disabled._start = Mock(side_effect=AssertionError("must not start"))
    assert disabled.annotate(tmp_path / "unused.png", [region]) == [region]
    assert region.metadata["text_classification"]["status"] == "disabled"
    unavailable = LayoutTextClassifier({"text_classification": {"python_executable": str(tmp_path / "missing")}})
    unavailable._start = Mock(side_effect=RuntimeError("runtime_not_installed"))
    for _ in range(2):
        assert unavailable.annotate(tmp_path / "unused.png", [region]) == [region]
        assert region.metadata["text_classification"]["excluded"] is False
    unavailable._start.assert_called_once()
    assert region.metadata["text_classification"]["status"] == "unavailable"


def test_incomplete_response_is_fail_open(tmp_path):
    classifier = LayoutTextClassifier({})
    classifier._process = Mock()
    classifier._receive = Mock(return_value={"classifications": []})
    classifier.close = Mock()
    region = DetectionResult(POLYGON, [15, 20, 20, 30], 0.9)
    classifier.annotate(tmp_path / "unused.png", [region])
    assert region.metadata["text_classification"]["status"] == "unavailable"
    assert region.metadata["text_classification"]["excluded"] is False


def test_classification_can_recover_after_cooldown_and_manual_reset(tmp_path, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr("app.services.detection.classification.time.monotonic", lambda: clock[0])
    classifier = LayoutTextClassifier({})
    classifier._start = Mock(side_effect=RuntimeError("worker_timeout"))
    region = DetectionResult(POLYGON, [15, 20, 20, 30], 0.9)
    classifier.annotate(tmp_path / "unused.png", [region])
    classifier.annotate(tmp_path / "unused.png", [region])
    assert classifier._start.call_count == 1
    clock[0] = 61
    classifier.annotate(tmp_path / "unused.png", [region])
    assert classifier._start.call_count == 2
    classifier.reset()
    classifier.annotate(tmp_path / "unused.png", [region])
    assert classifier._start.call_count == 3


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
        item = ModelConfig(kind="detection", name="retry-test", provider="paddleocr", config={})
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


def test_paddle_filters_before_bubble_grouping(tmp_path, monkeypatch):
    from app.services.base import ProviderState
    from app.services.detection.providers import PaddleTextDetector

    path = tmp_path / "page.webp"
    Image.new("RGB", (120, 100), "white").save(path)
    detector = PaddleTextDetector({"bubble_grouping": False})
    detector.state = ProviderState.READY
    detector._model = Mock()
    detector._model.predict.return_value = [{"dt_polys": [POLYGON], "dt_scores": [0.9]}]

    def annotate(path, regions, **kwargs):
        regions[0].metadata["text_classification"] = {"label": "onomatopoeia", "status": "classified", "excluded": True}
        return regions

    monkeypatch.setattr(detector._text_classifier, "annotate", annotate)
    detected = detector.detect(path)
    kept, report = partition_text_roles(detected)
    assert not kept
    assert report["excluded_count"] == 1

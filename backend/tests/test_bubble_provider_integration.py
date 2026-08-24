from __future__ import annotations

from pathlib import Path

import numpy as np
from app.services.base import ProviderState
from app.services.detection.bubbles import BubbleInstance
from app.services.detection.providers import PaddleTextDetector
from app.utils.geometry import bbox_to_polygon

PAGE_SHAPE = (240, 320)
TEXT_POLYGONS = [
    bbox_to_polygon([132, 45, 20, 70]),
    bbox_to_polygon([96, 58, 22, 82]),
]


class FakePaddleModel:
    def predict(self, _image_path: str) -> list[dict[str, object]]:
        return [
            {
                "dt_polys": TEXT_POLYGONS,
                "dt_scores": [0.94, 0.87],
            }
        ]


class FakeBubbleSegmenter:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = 0

    def segment(self, _image_path: Path) -> list[BubbleInstance]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [
            BubbleInstance(
                instance_id="same-balloon",
                bbox=[70.0, 20.0, 120.0, 160.0],
                confidence=0.95,
                polygon=bbox_to_polygon([70, 20, 120, 160]),
                mask=np.ones((160, 120), dtype=np.uint8),
                mask_origin=(70, 20),
                image_shape=PAGE_SHAPE,
            )
        ]


def ready_detector(
    config: dict[str, object] | None = None,
    *,
    segmenter: FakeBubbleSegmenter,
) -> PaddleTextDetector:
    detector = PaddleTextDetector(config)
    detector._model = FakePaddleModel()
    detector._bubble_segmenter = segmenter
    detector.state = ProviderState.READY
    return detector


def test_missing_bubble_grouping_config_defaults_to_enabled_and_merges_same_bubble(tmp_path: Path) -> None:
    segmenter = FakeBubbleSegmenter()
    detector = ready_detector({}, segmenter=segmenter)

    detected = detector.detect(tmp_path / "page.png")

    assert segmenter.calls == 1
    assert len(detected) == 1
    assert detected[0].bbox == [96.0, 45.0, 56.0, 95.0]
    assert detected[0].bubble_id is not None
    assert detected[0].metadata["balloon_assignment"]["status"] == "assigned"
    assert detected[0].metadata["line_grouping"]["source_count"] == 2


def test_explicitly_disabled_bubble_grouping_does_not_call_segmenter(tmp_path: Path) -> None:
    segmenter = FakeBubbleSegmenter(error=AssertionError("segmenter must not be called"))
    detector = ready_detector({"bubble_grouping": {"enabled": False}}, segmenter=segmenter)

    detected = detector.detect(tmp_path / "page.png")

    assert segmenter.calls == 0
    assert len(detected) == 2
    assert [region.bbox for region in detected] == [[132.0, 45.0, 20.0, 70.0], [96.0, 58.0, 22.0, 82.0]]
    assert all(region.bubble_id is None for region in detected)
    assert all("balloon_assignment" not in region.metadata for region in detected)


def test_segmentation_failure_keeps_original_boxes_and_marks_grouping_unavailable(tmp_path: Path) -> None:
    segmenter = FakeBubbleSegmenter(error=RuntimeError("simulated segmentation failure"))
    detector = ready_detector({}, segmenter=segmenter)

    detected = detector.detect(tmp_path / "page.png")
    second_page = detector.detect(tmp_path / "page-2.png")

    assert segmenter.calls == 1
    assert len(detected) == 2
    assert [region.bbox for region in detected] == [[132.0, 45.0, 20.0, 70.0], [96.0, 58.0, 22.0, 82.0]]
    assert all(region.bubble_id is None for region in detected)
    assert all(
        region.metadata["balloon_assignment"]
        == {
            "status": "unavailable",
            "bubble_id": None,
            "reason": "segmentation_failed",
        }
        for region in detected
    )
    assert all(
        region.metadata["balloon_assignment"]["reason"] == "segmentation_failed"
        for region in second_page
    )


def test_invalid_grouping_config_falls_back_without_failing_text_detection(tmp_path: Path) -> None:
    segmenter = FakeBubbleSegmenter()
    detector = ready_detector(
        {"bubble_grouping": {"enabled": True, "mask_padding": -1}},
        segmenter=segmenter,
    )

    detected = detector.detect(tmp_path / "page.png")

    assert segmenter.calls == 1
    assert len(detected) == 2
    assert all(region.bubble_id is None for region in detected)
    assert all(
        region.metadata["balloon_assignment"]["reason"] == "segmentation_failed"
        for region in detected
    )

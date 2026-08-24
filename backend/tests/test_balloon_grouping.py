import numpy as np
from app.services.base import DetectionResult
from app.services.detection.bubbles import BubbleInstance
from app.services.detection.grouping import group_text_regions_by_bubbles
from app.utils.geometry import bbox_to_polygon

PAGE_SHAPE = (240, 320)


def text_region(
    bbox: list[float],
    *,
    orientation: str = "vertical",
    confidence: float = 0.9,
) -> DetectionResult:
    return DetectionResult(
        polygon=bbox_to_polygon(bbox),
        bbox=bbox,
        confidence=confidence,
        orientation=orientation,
    )


def rectangular_bubble(
    instance_id: str,
    bbox: list[int],
    *,
    confidence: float = 0.9,
) -> BubbleInstance:
    x, y, width, height = bbox
    return BubbleInstance(
        instance_id=instance_id,
        bbox=[float(x), float(y), float(width), float(height)],
        confidence=confidence,
        polygon=bbox_to_polygon(bbox),
        mask=np.ones((height, width), dtype=np.uint8),
        mask_origin=(x, y),
        image_shape=PAGE_SHAPE,
    )


def test_merges_multiple_text_regions_assigned_to_the_same_bubble() -> None:
    regions = [
        text_region([132, 45, 20, 70], confidence=0.94),
        text_region([96, 58, 22, 82], confidence=0.87),
    ]
    grouped = group_text_regions_by_bubbles(
        regions,
        [rectangular_bubble("source-bubble-1", [70, 20, 120, 160])],
        page_key="page-same-bubble",
    )

    assert len(grouped) == 1
    merged = grouped[0]
    assert merged.bubble_id is not None
    assert merged.bbox == [96, 45, 56, 95]
    assert merged.confidence == 0.87
    assert merged.metadata["balloon_assignment"]["status"] == "assigned"
    assert merged.metadata["line_grouping"]["method"] == "balloon_instance"
    assert merged.metadata["line_grouping"]["source_count"] == 2


def test_does_not_merge_text_regions_assigned_to_different_bubbles() -> None:
    regions = [
        text_region([45, 55, 24, 72]),
        text_region([235, 55, 24, 72]),
    ]
    grouped = group_text_regions_by_bubbles(
        regions,
        [
            rectangular_bubble("left", [20, 25, 90, 145]),
            rectangular_bubble("right", [205, 25, 90, 145]),
        ],
        page_key="page-different-bubbles",
    )

    assert len(grouped) == 2
    assert all(region.bubble_id is not None for region in grouped)
    assert grouped[0].bubble_id != grouped[1].bubble_id
    assert [region.bbox for region in grouped] == [region.bbox for region in regions]


def test_does_not_merge_overlapping_text_regions_outside_all_bubbles() -> None:
    regions = [
        text_region([70, 70, 80, 48], orientation="horizontal"),
        text_region([105, 88, 90, 48], orientation="horizontal"),
    ]
    grouped = group_text_regions_by_bubbles(
        regions,
        [],
        page_key="page-outside",
    )

    assert len(grouped) == 2
    assert [region.bbox for region in grouped] == [region.bbox for region in regions]
    assert all(region.bubble_id is None for region in grouped)
    assert all(region.metadata["balloon_assignment"]["status"] == "outside" for region in grouped)


def test_does_not_merge_regions_with_ambiguous_two_bubble_assignment() -> None:
    regions = [
        text_region([112, 70, 26, 72]),
        text_region([128, 82, 26, 72]),
    ]
    # Identical instance masks deliberately give both candidates the same
    # containment and score, so neither text region may be assigned by guess.
    bubbles = [
        rectangular_bubble("candidate-a", [80, 35, 120, 155]),
        rectangular_bubble("candidate-b", [80, 35, 120, 155]),
    ]

    grouped = group_text_regions_by_bubbles(
        regions,
        bubbles,
        page_key="page-ambiguous",
    )

    assert len(grouped) == 2
    assert all(region.bubble_id is None for region in grouped)
    assert all(region.metadata["balloon_assignment"]["status"] == "ambiguous" for region in grouped)
    assert all(region.metadata["balloon_assignment"]["second_score"] is not None for region in grouped)


def test_vertical_members_are_sorted_right_to_left_and_preserve_source_polygons() -> None:
    left = text_region([55, 42, 20, 82])
    right_bottom = text_region([125, 105, 22, 65])
    right_top = text_region([125, 35, 22, 55])
    grouped = group_text_regions_by_bubbles(
        [left, right_bottom, right_top],
        [rectangular_bubble("vertical-balloon", [30, 15, 155, 190])],
        page_key="page-vertical-order",
    )

    assert len(grouped) == 1
    grouping = grouped[0].metadata["line_grouping"]
    # Source geometry stays aligned to detector input order so masks can be
    # regenerated deterministically; member_order is the reading-order
    # permutation into that source list (right column top-down, then left).
    source_members = [left, right_bottom, right_top]
    assert grouping["source_boxes"] == [member.bbox for member in source_members]
    assert grouping["source_polygons"] == [member.polygon for member in source_members]
    assert grouping["member_order"] == [2, 1, 0]


def test_invalid_text_polygon_stays_independent_without_crashing() -> None:
    invalid = DetectionResult(
        polygon=[[float("nan"), 20], [40, 20], [40, 60]],
        bbox=[20, 20, 20, 40],
        confidence=0.8,
    )

    grouped = group_text_regions_by_bubbles(
        [invalid],
        [rectangular_bubble("nearby", [10, 10, 80, 80])],
        page_key="invalid-polygon",
    )

    assert len(grouped) == 1
    assert grouped[0].bubble_id is None
    assert grouped[0].metadata["balloon_assignment"] == {
        "status": "outside",
        "bubble_id": None,
        "reason": "invalid_polygon",
    }


def test_low_confidence_bubble_cannot_group_text_regions() -> None:
    regions = [text_region([80, 40, 20, 60]), text_region([115, 40, 20, 60])]
    grouped = group_text_regions_by_bubbles(
        regions,
        [rectangular_bubble("weak-false-positive", [50, 20, 120, 120], confidence=0.2)],
        page_key="weak-bubble",
    )

    assert len(grouped) == 2
    assert all(region.bubble_id is None for region in grouped)

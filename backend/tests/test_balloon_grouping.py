from pathlib import Path

import cv2
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


def masked_bubble(
    instance_id: str,
    bbox: list[int],
    mask: np.ndarray,
    *,
    confidence: float = 0.95,
) -> BubbleInstance:
    x, y, width, height = bbox
    assert mask.shape == (height, width)
    return BubbleInstance(
        instance_id=instance_id,
        bbox=[float(value) for value in bbox],
        confidence=confidence,
        polygon=bbox_to_polygon(bbox),
        mask=mask.astype(np.uint8),
        mask_origin=(x, y),
        image_shape=PAGE_SHAPE,
    )


def full_constraint(region: DetectionResult) -> np.ndarray:
    assert region.balloon_mask is not None
    assert region.balloon_mask_origin is not None
    output = np.zeros(PAGE_SHAPE, dtype=np.uint8)
    x, y = region.balloon_mask_origin
    height, width = region.balloon_mask.shape
    output[y : y + height, x : x + width] = region.balloon_mask
    return output


def test_merges_multiple_text_regions_assigned_to_the_same_bubble() -> None:
    regions = [
        text_region([132, 45, 20, 70], orientation="horizontal", confidence=0.94),
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
    assert merged.metadata["line_grouping"]["source_orientations"] == ["horizontal", "vertical"]


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


def test_distinct_instance_masks_with_same_proposal_bbox_never_share_stable_id() -> None:
    left_mask = np.zeros((160, 160), dtype=np.uint8)
    right_mask = np.zeros_like(left_mask)
    cv2.ellipse(left_mask, (48, 80), (42, 68), 0, 0, 360, 1, -1)
    cv2.ellipse(right_mask, (112, 80), (42, 68), 0, 0, 360, 1, -1)
    bubbles = [
        masked_bubble("proposal-left", [70, 30, 160, 160], left_mask),
        masked_bubble("proposal-right", [70, 30, 160, 160], right_mask),
    ]
    regions = [
        text_region([94, 70, 18, 70]),
        text_region([188, 70, 18, 70]),
    ]

    grouped = group_text_regions_by_bubbles(regions, bubbles, page_key="same-proposal-bbox")

    assert len(grouped) == 2
    assert all(region.metadata["balloon_assignment"]["status"] == "assigned" for region in grouped)
    assert grouped[0].bubble_id != grouped[1].bubble_id
    assert all("line_grouping" not in region.metadata for region in grouped)


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


def test_medium_confidence_bubble_cannot_merge_text_before_it_is_safe_for_repair() -> None:
    regions = [text_region([80, 40, 20, 60]), text_region([115, 40, 20, 60])]
    grouped = group_text_regions_by_bubbles(
        regions,
        [rectangular_bubble("uncertain", [50, 20, 120, 120], confidence=0.5)],
        page_key="medium-confidence-bubble",
        min_bubble_confidence=0.35,
    )

    assert len(grouped) == 2
    assert all(region.bubble_id is None for region in grouped)
    assert all(region.metadata["balloon_assignment"]["status"] == "outside" for region in grouped)


def test_splits_two_balloon_lobes_joined_by_a_narrow_mask_bridge() -> None:
    mask = np.zeros((160, 260), dtype=np.uint8)
    cv2.ellipse(mask, (65, 80), (55, 65), 0, 0, 360, 1, -1)
    cv2.ellipse(mask, (195, 80), (55, 65), 0, 0, 360, 1, -1)
    cv2.rectangle(mask, (116, 76), (144, 84), 1, -1)
    regions = [
        text_region([73, 55, 22, 90]),
        text_region([204, 55, 22, 90]),
    ]

    grouped = group_text_regions_by_bubbles(
        regions,
        [masked_bubble("connected-lobes", [20, 20, 260, 160], mask)],
        page_key="page-connected-lobes",
    )

    assert [region.bbox for region in grouped] == [region.bbox for region in regions]
    assert len({region.bubble_id for region in grouped}) == 2
    assert all(region.metadata["instance_split"]["method"] == "mask_neck" for region in grouped)
    assert all(region.metadata["balloon_assignment"]["status"] == "assigned" for region in grouped)
    child_masks = [full_constraint(region) for region in grouped]
    parent = np.zeros(PAGE_SHAPE, dtype=np.uint8)
    parent[20:180, 20:280] = mask
    assert cv2.countNonZero(cv2.bitwise_and(child_masks[0], child_masks[1])) == 0
    assert np.array_equal(cv2.bitwise_or(child_masks[0], child_masks[1]), parent)
    assert all(region.balloon_mask_id == region.bubble_id for region in grouped)


def test_keeps_multiple_vertical_columns_inside_one_wide_balloon(tmp_path: Path) -> None:
    mask = np.zeros((180, 220), dtype=np.uint8)
    cv2.ellipse(mask, (110, 90), (105, 85), 0, 0, 360, 1, -1)
    regions = [
        text_region([210, 45, 22, 105]),
        text_region([160, 55, 22, 110]),
        text_region([110, 65, 22, 95]),
    ]
    image = np.full(PAGE_SHAPE, 255, dtype=np.uint8)
    cv2.ellipse(image, (160, 110), (105, 85), 0, 0, 360, 0, 2)
    image_path = tmp_path / "wide-balloon.png"
    assert cv2.imwrite(str(image_path), image)

    grouped = group_text_regions_by_bubbles(
        regions,
        [masked_bubble("wide-balloon", [50, 20, 220, 180], mask)],
        page_key="page-wide-balloon",
        image_path=image_path,
    )

    assert len(grouped) == 1
    assert grouped[0].bbox == [110, 45, 122, 120]
    assert grouped[0].metadata["line_grouping"]["source_count"] == 3
    assert grouped[0].metadata["line_grouping"]["member_order"] == [0, 1, 2]
    assert "instance_split" not in grouped[0].metadata


def test_nearby_overlapping_text_outside_a_real_bubble_stays_independent() -> None:
    mask = np.zeros((150, 100), dtype=np.uint8)
    cv2.ellipse(mask, (50, 75), (45, 70), 0, 0, 360, 1, -1)
    regions = [
        text_region([125, 70, 75, 55], orientation="horizontal"),
        text_region([165, 95, 85, 55], orientation="horizontal"),
    ]

    grouped = group_text_regions_by_bubbles(
        regions,
        [masked_bubble("nearby", [20, 30, 100, 150], mask)],
        page_key="page-nearby-outside",
    )

    assert [region.bbox for region in grouped] == [region.bbox for region in regions]
    assert all(region.bubble_id is None for region in grouped)
    assert all(region.metadata["balloon_assignment"]["status"] == "outside" for region in grouped)


def test_splits_solid_union_mask_with_an_anchored_internal_boundary_path(tmp_path: Path) -> None:
    mask = np.ones((200, 220), dtype=np.uint8)
    bubble = masked_bubble("wide-connected", [40, 20, 220, 200], mask)
    regions = [
        text_region([170, 40, 24, 120]),
        text_region([198, 40, 24, 120]),
        text_region([226, 40, 24, 120]),
        text_region([90, 125, 24, 90]),
        text_region([122, 126, 28, 88]),
    ]
    image = np.full(PAGE_SHAPE, 255, dtype=np.uint8)
    cv2.line(image, (160, 20), (160, 126), 0, 2)
    image_path = tmp_path / "connected.png"
    assert cv2.imwrite(str(image_path), image)

    grouped = group_text_regions_by_bubbles(
        regions,
        [bubble],
        page_key="page-visible-boundary",
        image_path=image_path,
        split_max_neck_ratio=0,
    )

    assert len(grouped) == 2
    assert [region.metadata["line_grouping"]["source_count"] for region in grouped] == [3, 2]
    assert len({region.bubble_id for region in grouped}) == 2
    assert all(
        region.metadata["line_grouping"]["instance_split"]["method"] == "text_gap_boundary"
        for region in grouped
    )
    child_masks = [full_constraint(region) for region in grouped]
    parent = np.zeros(PAGE_SHAPE, dtype=np.uint8)
    parent[20:220, 40:260] = mask
    assert cv2.countNonZero(cv2.bitwise_and(child_masks[0], child_masks[1])) == 0
    assert np.array_equal(cv2.bitwise_or(child_masks[0], child_masks[1]), parent)
    assert all(
        region.metadata["line_grouping"]["instance_split"]["cuts"][0]["boundary_source"]
        == "anchored_internal_line"
        for region in grouped
    )

    repeated = group_text_regions_by_bubbles(
        regions,
        [bubble],
        page_key="page-visible-boundary",
        image_path=image_path,
        split_max_neck_ratio=0,
    )
    assert [region.bubble_id for region in repeated] == [region.bubble_id for region in grouped]


def test_splits_overlapping_expanded_columns_at_the_shorter_visible_cap(tmp_path: Path) -> None:
    mask = np.ones((200, 160), dtype=np.uint8)
    bubble = masked_bubble("overlapping-columns", [40, 20, 160, 200], mask)
    regions = [
        text_region([90, 40, 32, 60]),
        text_region([65, 75, 32, 115]),
        text_region([40, 75, 32, 100]),
    ]
    image = np.full(PAGE_SHAPE, 255, dtype=np.uint8)
    # The expanded source boxes overlap around x=94, but their 70% text
    # cores leave a real gap. Only the shorter leading cap contains a seam.
    cv2.line(image, (94, 20), (94, 75), 0, 2)
    image_path = tmp_path / "overlapping-columns.png"
    assert cv2.imwrite(str(image_path), image)

    grouped = group_text_regions_by_bubbles(
        regions,
        [bubble],
        page_key="page-overlapping-columns",
        image_path=image_path,
        split_max_neck_ratio=0,
    )

    assert len(grouped) == 2
    assert [
        region.metadata.get("line_grouping", {}).get("source_count", 1) for region in grouped
    ] == [1, 2]
    assert len({region.bubble_id for region in grouped}) == 2
    split = grouped[0].metadata["instance_split"]
    assert split["orientation"] == "vertical"
    assert split["cuts"][0]["cap_kind"] == "leading"


def test_splits_stacked_balloons_even_when_their_text_is_vertical(tmp_path: Path) -> None:
    mask = np.ones((200, 220), dtype=np.uint8)
    bubble = masked_bubble("stacked-balloons", [40, 20, 220, 200], mask)
    regions = [
        text_region([100, 40, 24, 80]),
        text_region([128, 45, 24, 75]),
        text_region([70, 145, 24, 70]),
        text_region([98, 145, 24, 70]),
    ]
    image = np.full(PAGE_SHAPE, 255, dtype=np.uint8)
    cv2.line(image, (40, 132), (114, 132), 0, 2)
    image_path = tmp_path / "stacked-balloons.png"
    assert cv2.imwrite(str(image_path), image)

    grouped = group_text_regions_by_bubbles(
        regions,
        [bubble],
        page_key="page-stacked-balloons",
        image_path=image_path,
        split_max_neck_ratio=0,
    )

    assert len(grouped) == 2
    assert [region.metadata["line_grouping"]["source_count"] for region in grouped] == [2, 2]
    assert len({region.bubble_id for region in grouped}) == 2
    assert all(
        region.metadata["line_grouping"]["instance_split"]["orientation"] == "horizontal"
        for region in grouped
    )


def test_staggered_columns_without_a_boundary_path_stay_in_one_balloon(tmp_path: Path) -> None:
    mask = np.ones((200, 220), dtype=np.uint8)
    bubble = masked_bubble("wide-connected", [40, 20, 220, 200], mask)
    regions = [
        text_region([170, 40, 24, 120]),
        text_region([198, 40, 24, 120]),
        text_region([226, 40, 24, 120]),
        text_region([90, 125, 24, 90]),
        text_region([122, 126, 28, 88]),
    ]
    image_path = tmp_path / "plain.png"
    assert cv2.imwrite(str(image_path), np.full(PAGE_SHAPE, 255, dtype=np.uint8))

    grouped = group_text_regions_by_bubbles(
        regions,
        [bubble],
        page_key="page-no-boundary",
        image_path=image_path,
        split_max_neck_ratio=0,
    )

    assert len(grouped) == 1
    assert grouped[0].metadata["line_grouping"]["source_count"] == 5
    assert "instance_split" not in grouped[0].metadata

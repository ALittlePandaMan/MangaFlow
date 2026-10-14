from pathlib import Path

import cv2
import numpy as np
import pytest
from app.services.base import DetectionResult
from app.services.detection.bubbles import BubbleInstance
from app.services.detection.grouping import group_text_regions_by_bubbles
from app.services.detection.outlines import refine_bubble_outlines
from app.utils.geometry import bbox_to_polygon

SHAPE = (240, 320)


def region(bbox: list[float]) -> DetectionResult:
    return DetectionResult(polygon=bbox_to_polygon(bbox), bbox=bbox, orientation="vertical", confidence=.9)


def bubble(mask: np.ndarray, origin=(40, 20), confidence=.65) -> BubbleInstance:
    h, w = mask.shape
    return BubbleInstance(instance_id="model", bbox=[*origin, w, h], polygon=bbox_to_polygon([*origin, w, h]),
                          mask=mask, mask_origin=origin, confidence=confidence, image_shape=SHAPE)


def save(tmp_path: Path, image: np.ndarray) -> Path:
    path = tmp_path / "page.png"
    assert cv2.imwrite(str(path), image)
    return path


def test_outline_recovers_weak_colored_balloon_without_inflating_model_confidence(tmp_path):
    mask = np.zeros((200, 200), np.uint8)
    cv2.ellipse(mask, (100, 100), (96, 96), 0, 0, 360, 1, -1)
    image = np.full((*SHAPE, 3), 255, np.uint8)
    cv2.ellipse(image, (140, 120), (96, 96), 0, 0, 360, (170, 205, 255), -1)
    cv2.ellipse(image, (140, 120), (96, 96), 0, 0, 360, (20, 20, 20), 2)
    # Overlapping detector columns in one balloon, not an overlap-only merge.
    regions = [region([145, 45, 45, 130]), region([110, 50, 45, 145]), region([85, 85, 35, 100])]
    path = save(tmp_path, image)
    grouped = group_text_regions_by_bubbles(regions, [bubble(mask)], image_path=path)
    assert len(grouped) == 1
    assert grouped[0].metadata["line_grouping"]["source_count"] == 3
    assignment = grouped[0].metadata["balloon_assignment"]
    assert assignment["outline_evidence"]["supported_sectors"] >= 3
    assert assignment["balloon_confidence"] == .65


@pytest.mark.parametrize("confidence", [.2, .5, .65])
def test_no_outline_or_very_weak_model_still_cannot_merge(tmp_path, confidence):
    image = np.full(SHAPE, 255, np.uint8)
    mask = np.ones((180, 180), np.uint8)
    regions = [region([80, 50, 50, 130]), region([115, 50, 50, 130])]
    if confidence < .6:
        cv2.rectangle(image, (40, 20), (219, 199), 0, 2)
    grouped = group_text_regions_by_bubbles(regions, [bubble(mask, confidence=confidence)],
                                           image_path=save(tmp_path, image))
    assert len(grouped) == 2
    assert all(r.bubble_id is None for r in grouped)


def test_divider_splits_model_union_before_assigning_a_crossing_text_box(tmp_path):
    image = np.full(SHAPE, 255, np.uint8)
    cv2.rectangle(image, (40, 20), (259, 219), 0, 2)
    # A real slanted wall, despite the model producing one solid union mask.
    cv2.line(image, (145, 20), (160, 219), 0, 3)
    mask = np.ones((200, 220), np.uint8)
    regions = [region([80, 50, 35, 135]), region([195, 50, 35, 135]), region([135, 60, 36, 140])]
    grouped = group_text_regions_by_bubbles(regions, [bubble(mask, confidence=.95)],
                                           image_path=save(tmp_path, image))
    assert len(grouped) == 3
    assert grouped[0].bubble_id != grouped[1].bubble_id
    assert grouped[2].bubble_id is None
    assert grouped[0].metadata["instance_split"]["method"] == "visible_interiors"
    assert all("line_grouping" not in r.metadata for r in grouped)
    masks = []
    for r in grouped[:2]:
        full = np.zeros(SHAPE, np.uint8)
        x, y = r.balloon_mask_origin
        h, w = r.balloon_mask.shape
        full[y:y+h, x:x+w] = r.balloon_mask
        masks.append(full)
    assert not np.any(masks[0] & masks[1])
    assert np.count_nonzero(masks[0] | masks[1]) == int(mask.sum())
    assert grouped[0].balloon_mask_parent_id == grouped[1].balloon_mask_parent_id
    assert grouped[0].balloon_mask_parent_id.startswith("bubble-")


def test_handwritten_stroke_touching_outline_is_not_a_balloon_divider(tmp_path):
    image = np.full(SHAPE, 255, np.uint8)
    cv2.rectangle(image, (40, 20), (259, 219), 0, 2)
    cv2.line(image, (150, 20), (150, 219), 0, 3)
    regions = [region([85, 40, 30, 160]), region([135, 20, 32, 200]), region([195, 40, 30, 160])]
    grouped = group_text_regions_by_bubbles(regions, [bubble(np.ones((200, 220), np.uint8), confidence=.95)],
                                           image_path=save(tmp_path, image))
    assert len(grouped) == 1
    assert "instance_split" not in grouped[0].metadata


def test_outline_verification_does_not_mutate_proposals_or_input_image():
    image = np.full(SHAPE, 255, np.uint8)
    cv2.rectangle(image, (40, 20), (259, 219), 0, 2)
    cv2.line(image, (150, 20), (150, 219), 0, 3)
    original = image.copy()
    proposal = bubble(np.ones((200, 220), np.uint8), confidence=.95)
    mask = proposal.mask.copy()
    refine_bubble_outlines([proposal], [region([80, 50, 30, 135]), region([195, 50, 30, 135])], image)
    assert np.array_equal(image, original)
    assert np.array_equal(proposal.mask, mask)
    assert proposal.outline_evidence is None
    assert proposal.split_details is None


def test_explicit_split_opt_out_remains_respected(tmp_path):
    image = np.full(SHAPE, 255, np.uint8)
    cv2.rectangle(image, (40, 20), (259, 219), 0, 2)
    cv2.line(image, (150, 20), (150, 219), 0, 3)
    grouped = group_text_regions_by_bubbles([region([80, 50, 30, 135]), region([195, 50, 30, 135])],
        [bubble(np.ones((200, 220), np.uint8), confidence=.95)], image_path=save(tmp_path, image),
        split_connected_instances=False)
    assert len(grouped) == 1


FIXTURES = Path(__file__).parent / "fixtures" / "balloon-grouping"


def handwritten_fixture():
    image_path = FIXTURES / "handwritten-neighbors.png"
    shape = cv2.imread(str(image_path), 0).shape
    polygons = [
        [[61, 21], [93, 20], [98, 161], [66, 162]],
        [[90, 21], [115, 20], [118, 68], [93, 69]],
        [[38, 160], [88, 157], [98, 285], [47, 289]],
        [[78, 164], [118, 162], [123, 270], [83, 271]],
        [[21, 204], [48, 202], [54, 287], [28, 289]],
    ]
    boxes = [[61, 20, 37, 142], [90, 20, 28, 49], [38, 157, 60, 132],
             [78, 162, 45, 109], [21, 202, 33, 87]]
    regions = [DetectionResult(polygon=p, bbox=b, orientation="vertical", confidence=.9)
               for p, b in zip(polygons, boxes, strict=True)]
    bubbles = []
    for name, origin, confidence in [("upper", (40, 15), .782587), ("lower", (15, 153), .654595)]:
        mask = (cv2.imread(str(FIXTURES / f"handwritten-{name}-mask.png"), 0) > 0).astype(np.uint8)
        h, w = mask.shape
        bubbles.append(BubbleInstance(instance_id=name, bbox=[*origin, w, h],
            polygon=bbox_to_polygon([*origin, w, h]), mask=mask, mask_origin=origin,
            confidence=confidence, image_shape=shape))
    return image_path, regions, bubbles


def test_user_handwritten_overlaps_merge_but_adjacent_balloon_stays_separate():
    # Original crop + actual model masks: the lower balloon scored only .655.
    image_path, regions, bubbles = handwritten_fixture()
    grouped = group_text_regions_by_bubbles(regions, bubbles, image_path=image_path, page_key="handwritten")
    assert len(grouped) == 2
    assert [r.metadata["line_grouping"]["source_count"] for r in grouped] == [2, 3]
    assert grouped[0].bubble_id != grouped[1].bubble_id
    assert grouped[1].bbox == [21, 157, 102, 132]
    assert grouped[1].metadata["line_grouping"]["member_order"] == [1, 0, 2]
    assert grouped[1].metadata["balloon_assignment"]["outline_evidence"]["support"] >= .75
    assert all(r.metadata["balloon_grouping_version"] == 3 for r in grouped)
    repeated = group_text_regions_by_bubbles(regions, bubbles, image_path=image_path, page_key="handwritten")
    assert [r.bubble_id for r in repeated] == [r.bubble_id for r in grouped]


def test_stricter_explicit_confidence_threshold_is_not_overridden_by_outline():
    image_path, regions, bubbles = handwritten_fixture()
    grouped = group_text_regions_by_bubbles(regions, bubbles, image_path=image_path, min_bubble_confidence=.9)
    assert len(grouped) == 5
    assert all(r.bubble_id is None for r in grouped)


def test_user_adjacent_balloon_wall_splits_one_large_model_proposal():
    image_path, regions, bubbles = handwritten_fixture()
    mask = np.zeros(bubbles[0].image_shape, np.uint8)
    for b in bubbles:
        x, y = b.mask_origin
        h, w = b.mask.shape
        mask[y:y+h, x:x+w] |= b.mask
    # Simulate a segmentation model bridging the adjacent balloons.
    cv2.rectangle(mask, (65, 105), (105, 195), 1, -1)
    joined = BubbleInstance(instance_id="joined", bbox=[0, 0, mask.shape[1], mask.shape[0]],
        polygon=bbox_to_polygon([0, 0, mask.shape[1], mask.shape[0]]), mask=mask,
        mask_origin=(0, 0), confidence=.95, image_shape=mask.shape)
    grouped = group_text_regions_by_bubbles(regions, [joined], image_path=image_path,
                                           page_key="joined-handwritten")
    assert len(grouped) == 2
    assert [r.metadata["line_grouping"]["source_count"] for r in grouped] == [2, 3]
    assert grouped[0].bubble_id != grouped[1].bubble_id


def test_one_real_divider_does_not_authorize_splitting_a_neighboring_handwritten_stroke(tmp_path):
    image = np.full(SHAPE, 255, np.uint8)
    cv2.rectangle(image, (40, 20), (279, 219), 0, 2)
    cv2.line(image, (115, 20), (115, 219), 0, 3)  # Two balloons.
    cv2.line(image, (210, 20), (210, 219), 0, 3)  # A glyph touching the outline.
    regions = [region([70, 40, 28, 160]), region([150, 40, 28, 160]),
               region([195, 20, 32, 200]), region([235, 40, 28, 160])]
    grouped = group_text_regions_by_bubbles(regions, [bubble(np.ones((200, 240), np.uint8), confidence=.95)],
                                           image_path=save(tmp_path, image))
    assert len(grouped) == 2
    assert [r.metadata.get("line_grouping", {}).get("source_count", 1) for r in grouped] == [1, 3]
    assert grouped[0].bubble_id != grouped[1].bubble_id


def test_touching_handwritten_balloons_split_despite_mixed_orientation_and_punctuation():
    path = FIXTURES / "handwritten-touching.png"
    shape = cv2.imread(str(path), 0).shape
    mask = (cv2.imread(str(FIXTURES / "handwritten-touching-mask.png"), 0) > 0).astype(np.uint8)
    proposal = BubbleInstance(instance_id="touching", bbox=[12, 19, 166, 109],
        polygon=bbox_to_polygon([12, 19, 166, 109]), mask=mask, mask_origin=(12, 19),
        confidence=.929588, image_shape=shape)
    boxes = [[102, 23, 34, 79], [31, 56, 26, 36], [40, 49, 12, 8], [115, 30, 57, 63], [61, 61, 20, 18]]
    polygons = [
        [[102, 24], [134, 23], [136, 101], [105, 102]],
        [[31, 56], [57, 56], [57, 92], [31, 92]],
        [[40, 49], [52, 49], [52, 57], [40, 57]],
        [[115, 47], [149, 30], [172, 76], [138, 93]],
        [[61, 61], [81, 61], [81, 79], [61, 79]],
    ]
    orientations = ["vertical", "vertical", "horizontal", "horizontal", "horizontal"]
    confidences = [.539629, .610824, .309162, .7, .210258]
    regions = [DetectionResult(bbox=b, polygon=p, orientation=o, confidence=c)
               for b, p, o, c in zip(boxes, polygons, orientations, confidences, strict=True)]
    grouped = group_text_regions_by_bubbles(regions, [proposal], image_path=path)
    assert len(grouped) == 2
    assert sorted(r.metadata["line_grouping"]["source_count"] for r in grouped) == [2, 3]
    assert grouped[0].bubble_id != grouped[1].bubble_id


def test_staggered_mixed_text_in_one_outlined_balloon_does_not_split(tmp_path):
    image = np.full(SHAPE, 255, np.uint8)
    mask = np.zeros((200, 220), np.uint8)
    cv2.ellipse(mask, (110, 100), (105, 95), 0, 0, 360, 1, -1)
    cv2.ellipse(image, (150, 120), (105, 95), 0, 0, 360, 0, 2)
    regions = [region([195, 45, 25, 90]), region([90, 110, 25, 90]), region([145, 75, 25, 110])]
    punctuation = region([120, 155, 9, 10])
    punctuation.orientation = "horizontal"
    regions.append(punctuation)
    grouped = group_text_regions_by_bubbles(regions, [bubble(mask, confidence=.95)],
                                           image_path=save(tmp_path, image))
    assert len(grouped) == 1
    assert grouped[0].metadata["line_grouping"]["source_count"] == 4
    assert "instance_split" not in grouped[0].metadata

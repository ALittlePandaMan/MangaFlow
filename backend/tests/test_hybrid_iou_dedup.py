from itertools import permutations

import cv2
import numpy as np
import pytest
from app.services.detection.layout import build_layout_regions
from app.services.detection.mask_evidence import _box_iou
from app.services.detection.text_roles import LayoutPrediction


def samples(extra_mark=False):
    mask = np.zeros((110, 80), np.uint8)
    for y in range(16, 90, 18):
        mask[y:y+8, 30:38] = 1
    thick = cv2.dilate(mask, np.ones((3, 3), np.uint8))
    if extra_mark:
        thick[94:98, 56:60] = 1
    return [LayoutPrediction("text", .9, [10, 10, 60, 95], mask[10:105, 10:70].copy(), (10, 10)),
            LayoutPrediction("text", .85, [8, 8, 64, 99], thick[8:107, 8:72].copy(), (8, 8))]


def build(items):
    return build_layout_regions(items, 120, 150, {}, page_key="hybrid")


def test_iou_and_component_agreement_remove_thickness_variants_deterministically():
    items = samples()
    assert _box_iou(*items) > .8
    for values in permutations(items):
        result = build(list(values))
        assert len(result) == 1
        assert result[0].metadata["iou_deduplication"]["method"] == "box_iou_and_complete_lettering"
        # No union/dilation of the retained detector lettering or repair mask.
        raw = items[1]
        assert result[0].metadata["raw_bbox"] == raw.bbox
        np.testing.assert_array_equal(result[0].text_mask, raw.mask)


def test_high_box_iou_does_not_discard_an_unsupported_character():
    assert len(build(samples(extra_mark=True))) == 2


@pytest.mark.parametrize("kind", ["empty", "solid", "different_text", "different_bubbles", "different_panels"])
def test_iou_never_suppresses_without_compatible_lettering_and_ownership(kind):
    from app.services.detection.covered_text import duplicate_text_instances
    from app.services.detection.layout import text_geometry

    items = samples()
    if kind == "empty":
        items[0].mask[:] = 0
    elif kind == "solid":
        items[0].mask[:] = 1
    elif kind == "different_text":
        items[0].mask[:] = np.roll(items[0].mask, 20, axis=1)
    geometry = {i: text_geometry(item, 120, 150, padding=3) for i, item in enumerate(items)}
    removed = duplicate_text_instances(items, {0: 3, 1: 4} if kind == "different_bubbles" else {0: None, 1: None},
        geometry, {0: 1, 1: 2} if kind == "different_panels" else {0: None, 1: None}, bubble_coverage={0: 1., 1: 1.})
    assert not removed

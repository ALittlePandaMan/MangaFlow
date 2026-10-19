from __future__ import annotations

from copy import deepcopy

import numpy as np
import pytest
from app.services.detection.layout import build_layout_regions
from app.services.detection.mask_evidence import _coverage, _Mask, _window
from app.services.detection.mask_geometry import covered_components
from app.services.detection.text_roles import LayoutPrediction
from test_long_text import sample
from test_text_containment import shifted_duplicates
from test_unified_text_dedup import synthetic


def build(items, *, padding=3):
    return build_layout_regions(items, 400, 400, {"text_box_padding": padding}, page_key="review-regression")


def test_near_size_containment_with_imperfect_masks_still_prefers_complete_tighter_crop():
    child = shifted_duplicates()[0]
    pixels = np.pad(child.mask, 1)
    pixels[15, 6] = 1  # Parent-only edge stroke is also INSIDE the tighter crop.
    parent = LayoutPrediction("text", .99, [19, 19, 102, 52], pixels, (19, 19))
    result = build([parent, child])
    assert len(result) == 1
    assert result[0].metadata["raw_bbox"] == child.bbox


@pytest.mark.parametrize("stroke_in", ["parent", "losing_child"])
@pytest.mark.parametrize("padding", [0, 3, {"top": 0, "bottom": 12, "left": 2, "right": 0}])
def test_prose_preserves_every_original_edge_pixel_in_masks_and_actual_crops(stroke_in, padding):
    items = sample()
    if stroke_in == "parent":
        items[0].mask[4, 5] = 1
    else:
        alternative = deepcopy(items[1])
        alternative.confidence = .4
        alternative.mask[4, 5] = 1
        items.append(alternative)
    originals = deepcopy(items)
    result = build(items, padding=padding)
    assert sum(r.metadata.get("long_text_partition", {}).get("line_count", 0) for r in result) == 8
    lettering = [_Mask(r.text_mask, *r.text_mask_origin) for r in result]
    for region, mask in zip(result, lettering, strict=True):
        assert covered_components(mask, [region.polygon])[mask.pixels > 0].all()
        for member in region.duplicate_members:
            member_mask = _Mask(member.text_mask, *member.text_mask_origin)
            assert covered_components(member_mask, [member.polygon])[member_mask.pixels > 0].all()
    for item in items:
        mask = _window(item)
        assert _coverage(mask, lettering) == 1.
        assert covered_components(mask, [r.polygon for r in result])[mask.pixels > 0].all()
    for actual, original in zip(items, originals, strict=True):
        assert actual.bbox == original.bbox
        np.testing.assert_array_equal(actual.mask, original.mask)


def interlocking_balloons():
    first, second = np.zeros((160, 160), np.uint8), np.zeros((160, 160), np.uint8)
    first[5:155, 5:30] = first[130:155, 5:155] = 1
    second[5:30, 5:155] = second[5:155, 130:155] = 1
    items = [LayoutPrediction("bubble", .99, [0, 0, 160, 160], first, (0, 0)),
             LayoutPrediction("bubble", .98, [0, 0, 160, 160], second, (0, 0))]
    for x in [10, 135]:
        mask = np.zeros((60, 15), np.uint8)
        for y in [5, 22, 39]:
            mask[y:y+10, 3:12] = 1
        items.append(LayoutPrediction("text", .95, [x, 45, 15, 60], mask, (x, 45)))
    return items


def test_overlapping_balloon_boxes_do_not_erase_distinct_instance_masks_or_text_ownership():
    result = build(interlocking_balloons())
    assert len(result) == 2
    assert len({r.bubble_id for r in result}) == 2
    assert all(r.bubble_id and r.metadata["text_role"]["label"] == "dialogue" for r in result)


def test_jittered_duplicate_balloon_masks_do_not_block_parent_removal():
    items = synthetic()
    duplicate = deepcopy(items[-1])
    duplicate.bbox = [1, 1, 159, 179]
    duplicate.confidence = .8
    result = build([*items, duplicate])
    assert len(result) == 2
    assert len({r.bubble_id for r in result}) == 1


def test_complete_children_can_replace_parent_even_if_one_child_is_over_85_percent_of_parent_area():
    items = synthetic(children=((20, 20, 180, 12), (208, 20, 20, 12)))[:-1]
    result = build(items)
    assert len(result) == 2
    assert {tuple(r.metadata["raw_bbox"]) for r in result} == {tuple(p.bbox) for p in items[1:]}


@pytest.mark.parametrize("padding", [0, 3])
def test_cross_query_partial_strokes_do_not_hide_a_bisected_whole_glyph(padding):
    pixels = np.zeros((36, 140), np.uint8)
    for x in [6, 30, 65, 100, 122]:
        pixels[8:28, x:x+10] = 1
    pixels[:, 70] = 0
    alternative = pixels.copy()
    alternative[:, 69] = 0
    alternative[14, 70] = 1
    items = [LayoutPrediction("text", .8, [20, 20, 140, 36], pixels, (20, 20)),
             LayoutPrediction("text", .7, [20, 20, 70, 36], pixels[:, :70].copy(), (20, 20)),
             LayoutPrediction("text", .7, [90, 20, 70, 36], pixels[:, 70:].copy(), (90, 20)),
             LayoutPrediction("text", .4, [20, 20, 140, 36], alternative, (20, 20))]
    result = build(items, padding=padding)
    assert len(result) == 3
    assert any(r.metadata["raw_bbox"] == items[0].bbox for r in result)

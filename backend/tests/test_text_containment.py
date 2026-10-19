from __future__ import annotations

import json
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services.detection.covered_text import duplicate_text_instances
from app.services.detection.layout import RFDetrTextDetector, build_layout_regions, text_geometry
from app.services.detection.layout_protocol import decode_predictions
from app.services.detection.mask_evidence import _box_coverage, _window
from app.services.detection.text_roles import LayoutPrediction
from app.utils.geometry import bbox_to_polygon
from detection_assertions import assert_rebuilt_regions
from PIL import Image
from test_unified_text_dedup import synthetic


def build(items):
    return build_layout_regions(items, 400, 220, {}, page_key="containment-regression")


def overlapping_parent(*, unique=False, parent_wins=True, dialogue=False):
    mask = np.zeros((100, 100), np.uint8)
    for y in [5, 25, 45, 85, *([65] if unique else [])]:
        for x in range(5, 90, 12):
            mask[y:y+7, x:x+6] = 1
    items = [
        LayoutPrediction("text", .95 if parent_wins else .7, [20, 20, 100, 100], mask, (20, 20)),
        LayoutPrediction("text", .7 if parent_wins else .95, [20, 20, 100, 60], mask[:60].copy(), (20, 20)),
        LayoutPrediction("text", .65, [20, 95, 100, 25], mask[75:].copy(), (20, 95)),
    ]
    if dialogue:
        items.append(LayoutPrediction("bubble", .99, [0, 0, 160, 180], np.ones((180, 160), np.uint8), (0, 0)))
    return items


@pytest.mark.parametrize("dialogue", [False, True])
@pytest.mark.parametrize("parent_wins", [False, True])
@pytest.mark.parametrize("unique", [False, True])
def test_containment_cannot_discard_parent_child_evidence(dialogue, parent_wins, unique):
    items = overlapping_parent(unique=unique, parent_wins=parent_wins, dialogue=dialogue)
    expected = {tuple(p.bbox) for p in (items[:3] if unique else items[1:3])}
    for predictions in (items, list(reversed(items))):
        results = build(predictions)
        assert {tuple(r.metadata["raw_bbox"]) for r in results} == expected
        assert all((r.bubble_id is not None) == dialogue for r in results)


def shifted_duplicates():
    pixels = np.zeros((50, 100), np.uint8)
    for x in range(5, 90, 12):
        pixels[15:35, x:x+6] = 1
    return [
        LayoutPrediction("text", .95, [20, 20, 100, 50], pixels.copy(), (20, 20)),
        LayoutPrediction("text", .8, [21, 20, 100, 50], np.pad(pixels[:, 1:], ((0, 0), (0, 1))), (21, 20)),
    ]


def duplicates(items, *, bubble=None, panel=None, coverage=1., geometry=None):
    return duplicate_text_instances(items, bubble or dict.fromkeys(range(2)),
        geometry or {i: (bbox_to_polygon(p.bbox), "horizontal", 0.) for i, p in enumerate(items)},
        panel or dict.fromkeys(range(2)), bubble_coverage=dict.fromkeys(range(2), coverage))


def test_jittered_same_instance_is_still_deduplicated_without_mask_mutation():
    items = shifted_duplicates()
    before = deepcopy(items)
    assert duplicates(items) == {1}
    assert len(build(items)) == 1
    for actual, original in zip(items, before, strict=True):
        np.testing.assert_array_equal(actual.mask, original.mask)
        assert actual.bbox == original.bbox


@pytest.mark.parametrize("damage", ["punctuation", "empty", "solid", "bubble", "panel", "uncertain", "direction", "angle"])
def test_box_overlap_cannot_suppress_uncertain_or_unique_lettering(damage):
    items = shifted_duplicates()
    options = {}
    if damage == "punctuation":
        # Under 1% of foreground is unique, but outside the winning OCR crop.
        items[1].mask[24:26, -1] = 1
    elif damage in {"empty", "solid"}:
        for item in items:
            item.mask[:] = int(damage == "solid")
    elif damage == "bubble":
        options["bubble"] = {0: 3, 1: 4}
    elif damage == "panel":
        options["panel"] = {0: 3, 1: 4}
    elif damage == "uncertain":
        options.update(bubble={0: 3, 1: 3}, coverage=.94)
    else:
        options["geometry"] = {0: (bbox_to_polygon(items[0].bbox), "horizontal", 0.),
                               1: (bbox_to_polygon(items[1].bbox), "vertical" if damage == "direction" else "horizontal",
                                   11. if damage == "angle" else 0.)}
    assert duplicates(items, **options) == set()


@pytest.mark.parametrize("value", [0, 1])
def test_identical_crops_remain_deduplicated_without_segmentation(value):
    items = shifted_duplicates()
    items[0].mask[:] = value
    items[1] = deepcopy(items[0])
    items[1].confidence = .8
    assert duplicates(items) == {1}


def test_removing_duplicate_queries_preserves_bubble_panel_and_parent_resolution():
    items = synthetic()
    items.append(LayoutPrediction("panel", .99, [0, 0, 180, 200], np.ones((200, 180), np.uint8), (0, 0)))
    expected = build(items)
    duplicate = deepcopy(items[1])
    duplicate.confidence = .97  # Changes every structural index after sorting.
    for predictions in ([duplicate, *items], list(reversed([duplicate, *items]))):
        actual = build(predictions)
        assert len(actual) == len(expected) == 2
        assert [r.metadata["raw_bbox"] for r in actual] == [r.metadata["raw_bbox"] for r in expected]
        assert [r.bubble_id for r in actual] == [r.bubble_id for r in expected]
        assert [r.metadata["panel"] for r in actual] == [r.metadata["panel"] for r in expected]
        assert all("covered_parent_deduplication" in r.metadata for r in actual)
        for first, second in zip(actual, expected, strict=True):
            np.testing.assert_array_equal(first.text_mask, second.text_mask)
            np.testing.assert_array_equal(first.balloon_mask, second.balloon_mask)


def test_independent_parent_groups_do_not_share_cached_mask_evidence():
    left = synthetic()[:-1]
    right = deepcopy(left)
    for item in right:
        item.bbox[0] += 200
        item.mask_origin = (item.mask_origin[0]+200, item.mask_origin[1])
    results = build([*left, *right])
    assert len(results) == 4
    assert all("covered_parent_deduplication" in r.metadata for r in results)


@pytest.mark.parametrize("vertical", [False, True])
@pytest.mark.parametrize("unique", [False, True])
def test_partial_line_query_with_independent_sibling_is_not_prematurely_suppressed(vertical, unique):
    items = synthetic(children=((20, 20, 80, 12), (115, 20, 25, 12)))[:-1]
    items[0].confidence = .99
    if unique:
        items[0].mask[4:7, 88:90] = 1
    if vertical:
        for item in items:
            x, y, w, h = item.bbox
            item.bbox, item.mask_origin, item.mask = [y, x, h, w], (y, x), item.mask.T
    results = build(items)
    assert len(results) == (3 if unique else 2)
    if not unique:
        assert {tuple(r.metadata["raw_bbox"]) for r in results} == {tuple(p.bbox) for p in items[1:]}


def test_even_high_mask_agreement_cannot_erase_evidence_for_independent_short_phrase():
    items = synthetic(children=((20, 20, 110, 12), (132, 20, 8, 12)))[:-1]
    items[0].confidence = .99
    # The short phrase contributes under 10% of ink, so bidirectional aggregate
    # mask agreement alone would incorrectly classify its sibling as jitter.
    assert len(build(items)) == 3


def recorded_title():
    return decode_predictions(json.loads((Path(__file__).parent / "fixtures/contained_title_predictions.json").read_text()))


@pytest.mark.parametrize("padding", [0, 3, 50, {"top": 0, "bottom": 50, "left": 3, "right": 0}])
@pytest.mark.parametrize("parent_wins", [False, True])
def test_reported_title_removes_redundant_fragment_without_losing_complete_title_or_other_regions(padding, parent_wins):
    width, height, items = recorded_title()
    title = [p for p in items if p.label == "text" and 600 < p.bbox[0] < 700 and 500 < p.bbox[1] < 650]
    parent, child = sorted(title, key=lambda p: -p.bbox[2]*p.bbox[3])
    parent.confidence, child.confidence = (.99, .8) if parent_wins else (.3, .99)
    assert _box_coverage(child, parent) == pytest.approx(1.)
    assert child.bbox[2]*child.bbox[3] < .5*parent.bbox[2]*parent.bbox[3]
    before = deepcopy(items)
    result = build_layout_regions(items, width, height, {"text_box_padding": padding}, page_key="reported-title")
    active = [r for r in result if not r.metadata["text_classification"]["excluded"]]
    assert len(active) == 8
    assert [r.metadata["raw_bbox"] for r in active if r.metadata["raw_bbox"] in [parent.bbox, child.bbox]] == [parent.bbox]
    # Dialogue, SFX exclusions and every surviving original mask are unchanged.
    for region in active:
        raw = next(p for p in items if p.bbox == region.metadata["raw_bbox"])
        np.testing.assert_array_equal(region.text_mask, _window(raw).pixels)
    for actual, original in zip(items, before, strict=True):
        assert actual.bbox == original.bbox
        np.testing.assert_array_equal(actual.mask, original.mask)


def test_reported_title_decision_does_not_depend_on_prediction_order():
    width, height, items = recorded_title()
    expected = build_layout_regions(items, width, height, {}, page_key="reported-title")
    for variant in [list(reversed(items)), [*items[10:], *items[:10]]]:
        actual = build_layout_regions(variant, width, height, {}, page_key="reported-title")
        assert [(r.bbox, r.bubble_id) for r in actual] == [(r.bbox, r.bubble_id) for r in expected]


def test_structural_dedup_never_removes_text_queries():
    from app.services.detection.layout import _deduplicate_layout_instances

    _, _, items = recorded_title()
    result = _deduplicate_layout_instances(items)
    assert all(any(p is r for r in result) for p in items if p.label == "text")


@pytest.mark.parametrize("fraction", [.05, .3, .7])
@pytest.mark.parametrize("vertical", [False, True])
@pytest.mark.parametrize("parent_wins", [False, True])
def test_contained_fragment_does_not_require_similar_box_areas(fraction, vertical, parent_wins):
    pixels = np.zeros((30, 1000), np.uint8)
    for x in range(5, 990, 12):
        pixels[3:10, x:x+6] = pixels[15:23, x:x+6] = 1
    width = int(1000*fraction)
    items = [LayoutPrediction("text", .99 if parent_wins else .3, [20, 20, 1000, 30], pixels, (20, 20)),
             LayoutPrediction("text", .3 if parent_wins else .99, [20, 20, width, 30], pixels[:, :width].copy(), (20, 20))]
    if vertical:
        for item in items:
            x, y, w, h = item.bbox
            item.bbox, item.mask_origin, item.mask = [y, x, h, w], (y, x), item.mask.T
    geometry = {i: (bbox_to_polygon(p.bbox), "vertical" if vertical else "horizontal", 0.) for i, p in enumerate(items)}
    assert duplicates(items, geometry=geometry) == {1}


@pytest.mark.parametrize("near", [False, True])
def test_same_ink_in_a_tighter_single_child_prefers_the_child_not_the_large_blank_frame(near):
    child = shifted_duplicates()[0]
    offset = 1 if near else 10
    height, width = (52, 102) if near else (80, 200)
    pixels = np.zeros((height, width), np.uint8)
    pixels[offset:offset+50, offset:offset+100] = child.mask
    parent = LayoutPrediction("text", .99, [20-offset, 20-offset, width, height], pixels, (20-offset, 20-offset))
    result = build([parent, child])
    assert len(result) == 1
    assert result[0].metadata["raw_bbox"] == child.bbox


@pytest.mark.parametrize("unique", [False, True])
def test_removed_fragment_ink_survives_a_later_parent_replacement(unique):
    items = synthetic(children=((20, 20, 40, 30), (80, 20, 40, 30)))[:-1]
    root = items[0]
    pixels = root.mask[:, :70].copy()
    if unique:
        pixels[4:6, 49:51] = 1  # Tiny original strokes in the gap between leaf crops.
    fragment = LayoutPrediction("text", .4, [20, 20, 70, 30], pixels, (20, 20))
    result = build([*items, fragment])
    assert all(r.metadata["raw_bbox"] != fragment.bbox for r in result)
    if unique:
        assert len(result) == 3
        assert any(r.metadata["raw_bbox"] == root.bbox for r in result)
    else:
        assert len(result) == 2
        assert all(r.metadata["raw_bbox"] != root.bbox for r in result)


@pytest.mark.parametrize("protection", ["manual", "locked", "legacy"])
@pytest.mark.parametrize("scope", ["parent", "child"])
def test_reported_title_redetection_replaces_every_frame_without_losing_other_regions(client, monkeypatch, protection, scope):
    width, height, items = recorded_title()
    expected = [r for r in build_layout_regions(items, width, height, {}, page_key="title-protection")
                if not r.metadata["text_classification"]["excluded"]]
    title = [p for p in items if p.label == "text" and 600 < p.bbox[0] < 700 and 500 < p.bbox[1] < 650]
    chosen = sorted(title, key=lambda p: -p.bbox[2]*p.bbox[3])[int(scope == "child")]
    polygon = text_geometry(chosen, width, height, padding=3)[0]
    image = BytesIO()
    Image.new("RGB", (width, height), "white").save(image, "PNG")
    project = client.post("/api/projects", json={"name": "contained-title-protection"}).json()
    page = client.post(f"/api/projects/{project['id']}/images",
        files={"files": ("page.png", image.getvalue(), "image/png")}).json()[0]
    detector = RFDetrTextDetector({"device": "cpu"})
    detector._text_classifier.ensure_loaded = Mock()
    detector._text_classifier.detect_layout = Mock(return_value=(width, height, items))
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda *_: (detector, "rfdetr"))
    with SessionLocal() as db:
        row = TextRegion(image_id=page["id"], region_key="R001", reading_order=1,
            bbox=chosen.bbox, polygon=polygon, translated_bbox=chosen.bbox, translated_polygon=polygon,
            source_text="user original", translated_text="user translation", rotation=11., locked=protection == "locked",
            layout_data={"manual": True} if protection == "manual" else {"detection": {}} if protection == "locked" else {})
        db.add(row)
        db.commit()
        stored = db.get(ImagePage, page["id"])
        for _ in range(2):
            previous_ids = {r.id for r in stored.regions}
            PipelineProcessor(db).detect(stored, None, force=True)
            db.commit()
            db.refresh(stored)
            assert_rebuilt_regions(stored.regions, expected, previous_ids)
    detector.unload()

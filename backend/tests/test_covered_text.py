from __future__ import annotations

import json
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services.detection.covered_text import _frames_cover, covered_text_parents
from app.services.detection.layout import RFDetrTextDetector, build_layout_regions
from app.services.detection.layout_protocol import decode_predictions
from app.services.detection.nested_text import _coverage, _dilate, _window
from app.services.detection.text_roles import LayoutPrediction
from app.utils.geometry import bbox_to_polygon, intersection_area
from PIL import Image


def recorded(name="covered_text_predictions.json"):
    return decode_predictions(json.loads((Path(__file__).parent / "fixtures" / name).read_text()))


def build(items, width=592, height=840):
    return build_layout_regions(items, width, height, {}, page_key="covered-text-test")


def signature(results):
    return [(r.bbox, r.polygon, r.confidence, r.orientation, r.bubble_id, r.metadata) for r in results]


def sample():
    children = []
    for x, y, w, h in [(20, 20, 140, 28), (20, 80, 140, 35)]:
        pixels = np.zeros((h, w), np.uint8)
        for row in (5, 17):
            for col in range(5, w-8, 12):
                pixels[row:row+6, col:col+5] = 1
        children.append(LayoutPrediction("text", .8, [x, y, w, h], pixels, (x, y)))
    pixels = np.zeros((95, 140), np.uint8)
    for item in children:
        x, y, w, h = item.bbox
        pixels[y-20:y-20+h, x-20:x-20+w] |= item.mask
    return [LayoutPrediction("text", .4, [20, 20, 140, 95], pixels, (20, 20)), *children]


def geometries(items):
    return {i: (bbox_to_polygon([p.bbox[0]-3, p.bbox[1]-3, p.bbox[2]+6, p.bbox[3]+6]), "horizontal", 0.)
            for i, p in enumerate(items) if p.label == "text"}


def groups(items, *, bubbles=None, geometry=None, panels=None, reserved=None):
    geo = geometry if geometry is not None else geometries(items)
    return covered_text_parents(items, bubbles or dict.fromkeys(geo), geo, panels or dict.fromkeys(geo),
                                reserved=reserved or set())


def test_real_colophon_keeps_four_independent_frames_without_mutating_children():
    width, height, raw = recorded()
    original = deepcopy(raw)
    with patch("app.services.detection.layout.covered_text_parents", return_value={}):
        old = build(raw, width, height)
    assert len(old) == 5
    results = build(raw, width, height)
    assert len(results) == 4
    enclosing = old[0]
    assert enclosing.orientation == "vertical"  # Must not gate on the aggregate direction.
    for index, result in enumerate(results):
        previous = old[index+1]
        assert (result.bbox, result.polygon, result.confidence, result.orientation) == (
            previous.bbox, previous.polygon, previous.confidence, previous.orientation)
        assert not result.duplicate_members
        assert all(intersection_area(result.bbox, other.bbox) == 0 for other in results[index+1:])
    parent = next(p for p in raw if p.bbox == enclosing.metadata["raw_bbox"])
    assert _frames_cover(_window(parent), [r.polygon for r in results[:2]])
    assert all(r.metadata["covered_parent_deduplication"]["parent_bbox"] == parent.bbox for r in results[:2])
    assert all("covered_parent_deduplication" not in r.metadata for r in results[2:])
    for a, b in zip(raw, original, strict=True):
        assert a.bbox == b.bbox and a.mask_origin == b.mask_origin
        np.testing.assert_array_equal(a.mask, b.mask)


def test_real_output_is_deterministic_across_prediction_order():
    width, height, raw = recorded()
    expected = signature(build(raw, width, height))
    rng = np.random.default_rng(37)
    for _ in range(10):
        assert signature(build([raw[i] for i in rng.permutation(len(raw))], width, height)) == expected


def test_unassigned_parent_direction_can_differ_without_rotating_retained_children():
    items = sample()
    geo = geometries(items)
    geo[0] = (geo[0][0], "vertical", 20.)
    assert groups(items, geometry=geo) == {0: [2, 1]}


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("damage", ["empty", "zero", "solid"])
def test_unavailable_or_solid_masks_never_remove_candidates(index, damage):
    items = sample()
    if damage == "empty":
        items[index].mask, items[index].mask_origin = np.empty((0, 0), np.uint8), (0, 0)
    else:
        items[index].mask[:] = int(damage == "solid")
    assert groups(items) == {}


@pytest.mark.parametrize("index", [0, 1, 2])
def test_candidates_reserved_by_other_rules_cannot_be_deleted_or_used_as_evidence(index):
    assert groups(sample(), reserved={index}) == {}


@pytest.mark.parametrize("index", [0, 1, 2])
def test_any_bubble_assignment_preserves_existing_dialogue_rules(index):
    bubbles = dict.fromkeys(range(3))
    bubbles[index] = 99
    assert groups(sample(), bubbles=bubbles) == {}


def test_child_masks_must_agree_with_parent_and_cannot_supply_only_rectangular_coverage():
    items = sample()
    items[1].mask = np.roll(items[1].mask, 5, axis=1)
    assert groups(items) == {}


def test_tiny_unique_punctuation_in_gap_is_protected_despite_over_99_percent_coverage():
    items = sample()
    items[0].mask[45:47, 8:10] = 1
    root = _window(items[0])
    assert _coverage(root, [_dilate(_window(item)) for item in items[1:]]) > .99
    assert groups(items) == {}  # Cannot count the union's enclosing rectangle as coverage.


def test_complete_extra_line_is_not_discarded():
    items = sample()
    items[0].mask[45:51, 5:100] = 1
    assert groups(items) == {}


def test_rotated_final_frame_is_checked_not_its_axis_aligned_bbox():
    items = sample()
    geo = geometries(items)
    geo[1] = ([[17, 34], [90, 17], [163, 34], [90, 51]], "horizontal", 0.)
    assert groups(items, geometry=geo) == {}


@pytest.mark.parametrize("transpose", [False, True])
def test_union_coverage_cannot_remove_parent_when_one_glyph_is_cut_between_children(transpose):
    pixels = np.zeros((36, 140), np.uint8)
    for x in (6, 30, 54, 78, 102):
        pixels[8:28, x:x+20] = 1
        pixels[11:25, x+3:x+17] = 0
    items = [LayoutPrediction("text", .4, [20, 20, 140, 36], pixels, (20, 20)),
             LayoutPrediction("text", .8, [20, 28, 64, 20], pixels[8:28, :64].copy(), (20, 28)),
             LayoutPrediction("text", .8, [84, 28, 76, 20], pixels[8:28, 64:].copy(), (84, 28))]
    if transpose:
        for item in items:
            x, y, w, h = item.bbox
            item.bbox = [y, x, h, w]
            item.mask, item.mask_origin = item.mask.T.copy(), tuple(reversed(item.mask_origin))
    geo = geometries(items)
    assert _frames_cover(_window(items[0]), [geo[1][0], geo[2][0]])
    assert groups(items, geometry=geo) == {}
    assert len(build(items, 200, 200)) == 3


def test_padding_may_safely_complete_a_glyph_inside_one_surviving_child():
    items = sample()
    # A connected stroke crosses the raw child edge but fits its padded frame.
    items[0].mask[6:12, :7] = 1
    items[1].mask[6:12, :7] = 1
    assert groups(items) == {0: [2, 1]}


@pytest.mark.parametrize("kind", ["one_child", "duplicate_child", "partial_overlap", "nested_chain", "sfx"])
def test_ambiguous_candidate_graph_is_left_intact(kind):
    items = sample()
    if kind == "one_child":
        items.pop()
    else:
        extra = deepcopy(items[1] if kind != "nested_chain" else items[0])
        if kind == "partial_overlap":
            extra.bbox[0] -= 50
            extra.mask_origin = (extra.mask_origin[0]-50, extra.mask_origin[1])
        elif kind == "sfx":
            extra.label = "onomatopoeia"
        elif kind == "nested_chain":
            extra.bbox[3] -= 20
        items.append(extra)
    assert groups(items) == {}


@pytest.mark.parametrize("label", ["bubble", "panel"])
def test_even_unassigned_candidates_cannot_override_crossing_balloon_or_panel_geometry(label):
    items = sample()
    items.append(LayoutPrediction(label, .99, [0, 0, 80, 150], np.ones((150, 80), np.uint8), (0, 0)))
    assert groups(items) == {}


def test_one_containing_panel_is_allowed_but_different_child_panels_are_not():
    items = sample()
    items.append(LayoutPrediction("panel", .99, [0, 0, 200, 150], np.ones((150, 200), np.uint8), (0, 0)))
    assert groups(items, panels=dict.fromkeys(range(3), 3)) == {0: [2, 1]}
    assert groups(items, panels={0: 3, 1: 3, 2: 4}) == {}


@pytest.mark.parametrize("direction,angle", [("vertical", 0.), ("horizontal", 30.)])
def test_incompatible_child_typography_is_ambiguous(direction, angle):
    items = sample()
    geo = geometries(items)
    geo[2] = (geo[2][0], direction, angle)
    assert groups(items, geometry=geo) == {}


@pytest.mark.parametrize("fixture", ["nested_bubble_predictions.json", "long_text_predictions.json", "upright_text_predictions.json"])
def test_previous_dialogue_prose_and_angle_cases_are_identical(fixture):
    width, height, raw = recorded(fixture)
    enabled = build(raw, width, height)
    with patch("app.services.detection.layout.covered_text_parents", return_value={}):
        assert signature(build(raw, width, height)) == signature(enabled)
    if fixture == "nested_bubble_predictions.json":
        assert len(enabled) == 1 and len(enabled[0].duplicate_members) == 2
    elif fixture == "long_text_predictions.json":
        assert len(enabled) == 7
        assert sum(r.metadata.get("long_text_partition", {}).get("line_count", 0) for r in enabled) == 16


@pytest.mark.parametrize("protection", ["manual", "locked", "legacy"])
@pytest.mark.parametrize("scope", ["title", "publication", "whole"])
def test_redetection_keeps_user_regions_and_other_independent_text(client, monkeypatch, protection, scope):
    width, height, raw = recorded()
    results = build(raw, width, height)
    with patch("app.services.detection.layout.covered_text_parents", return_value={}):
        parent = build(raw, width, height)[0]
    chosen = {"title": results[0], "publication": results[1], "whole": parent}[scope]
    image = BytesIO()
    Image.new("RGB", (width, height), "white").save(image, "PNG")
    project = client.post("/api/projects", json={"name": "covered-text-protection"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("page.png", image.getvalue(), "image/png")}).json()[0]
    detector = RFDetrTextDetector({"device": "cpu"})
    detector._text_classifier.ensure_loaded = Mock()
    detector._text_classifier.detect_layout = Mock(return_value=(width, height, raw))
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda *_: (detector, "rfdetr"))
    with SessionLocal() as db:
        region = TextRegion(image_id=page["id"], region_key="R001", reading_order=1,
            bbox=chosen.bbox, polygon=chosen.polygon, translated_bbox=chosen.bbox, translated_polygon=chosen.polygon,
            source_text="user source", translated_text="user translation", rotation=11., locked=protection == "locked",
            layout_data={"manual": True} if protection == "manual" else {"detection": {}} if protection == "locked" else {})
        db.add(region)
        db.commit()
        identifier = region.id
        stored = db.get(ImagePage, page["id"])
        for _ in range(2):
            PipelineProcessor(db).detect(stored, None, force=True)
            db.commit()
            db.refresh(stored)
            protected = next(r for r in stored.regions if r.id == identifier)
            assert protected.bbox == chosen.bbox and protected.polygon == chosen.polygon and protected.rotation == 11.
            assert protected.source_text == "user source" and protected.translated_text == "user translation"
            others = [r for r in stored.regions if r.id != identifier]
            expected = results[2:] if scope == "whole" else [r for r in results if r is not chosen]
            assert [r.bbox for r in others] == [r.bbox for r in expected]
    detector.unload()

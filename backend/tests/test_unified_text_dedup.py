from __future__ import annotations

import json
from copy import deepcopy
from io import BytesIO
from itertools import permutations
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np
import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services.detection.covered_text import covered_text_parents
from app.services.detection.layout import RFDetrTextDetector, build_layout_regions, text_geometry
from app.services.detection.layout_protocol import decode_predictions
from app.services.detection.mask_evidence import _window
from app.services.detection.mask_geometry import covered_components
from app.services.detection.text_roles import LayoutPrediction
from app.services.translation.context import page_context
from app.utils.geometry import bbox_to_polygon
from detection_assertions import assert_rebuilt_regions
from PIL import Image
from test_covered_text import geometries as prose_geometries
from test_covered_text import groups as prose_groups
from test_covered_text import sample as prose_sample


def recorded(name="nested_bubble_predictions.json"):
    return decode_predictions(json.loads((Path(__file__).parent / "fixtures" / name).read_text()))


def build(items, width=160, height=180, config=None):
    return build_layout_regions(items, width, height, config or {}, page_key="unified-test")


def synthetic(children=((20, 20, 12, 80), (44, 20, 12, 80))):
    items = []
    for box in children:
        x, y, w, h = box
        mask = np.zeros((h, w), np.uint8)
        for row in range(3, h-8, 12):
            mask[row:row+8, 2:w-2] = 1
        items.append(LayoutPrediction("text", .8, list(box), mask, (x, y)))
    left, top = min(b[0] for b in children), min(b[1] for b in children)
    right, bottom = max(b[0]+b[2] for b in children), max(b[1]+b[3] for b in children)
    mask = np.zeros((bottom-top, right-left), np.uint8)
    for item in items:
        x, y, w, h = item.bbox
        mask[y-top:y-top+h, x-left:x-left+w] |= item.mask
    parent = LayoutPrediction("text", .5, [left, top, right-left, bottom-top], mask, (left, top))
    bubble = LayoutPrediction("bubble", .95, [0, 0, 160, 180], np.ones((180, 160), np.uint8), (0, 0))
    return [parent, *items, bubble]


def groups(items, *, bubbles=None, typography=None, coverage=None, reserved=None, panels=None):
    indices = [i for i, p in enumerate(items) if p.label == "text"]
    geometry = {i: text_geometry(items[i], 400, 400, padding=3) for i in indices}
    if typography:
        geometry = {i: (g[0], *typography.get(i, g[1:])) for i, g in geometry.items()}
    return covered_text_parents(items, bubbles if bubbles is not None else dict.fromkeys(indices, len(items)-1),
                                geometry, panels or dict.fromkeys(indices), reserved=reserved or set(),
                                bubble_coverage=coverage if coverage is not None else dict.fromkeys(indices, 1.))


@pytest.mark.parametrize("name", ["nested_bubble_predictions.json", "nested_uneven_bubble_predictions.json"])
def test_real_dialogue_removes_parent_and_keeps_complete_independent_columns_without_mutation(name):
    width, height, items = recorded(name)
    before = deepcopy(items)
    outputs = build(items, width, height)
    columns = [r for r in outputs if "covered_parent_deduplication" in r.metadata]
    assert len(columns) == 2
    assert all(r.metadata["covered_parent_deduplication"]["method"] == "complete_lettering_crops" for r in columns)
    assert columns[0].bubble_id == columns[1].bubble_id is not None
    parent_box = columns[0].metadata["covered_parent_deduplication"]["parent_bbox"]
    source = [p for p in items if p.bbox == parent_box or p.bbox in [r.metadata["raw_bbox"] for r in columns]]
    assert len(source) == 3
    for item in source:
        mask = _window(item)
        assert covered_components(mask, [r.polygon for r in columns])[mask.pixels > 0].all()
    assert all(r.metadata["raw_bbox"] != parent_box for r in outputs)
    assert all(not r.duplicate_members and "nested_deduplication" not in r.metadata for r in columns)
    # Surviving frames and their lettering masks are the original detections,
    # not a merged crop or a newly expanded repair mask.
    for result in columns:
        raw = next(p for p in items if p.bbox == result.metadata["raw_bbox"])
        assert result.polygon == text_geometry(raw, width, height, padding=3)[0]
        np.testing.assert_array_equal(result.text_mask, _window(raw).pixels)
    for first, second in zip(items, before, strict=True):
        assert first.bbox == second.bbox
        np.testing.assert_array_equal(first.mask, second.mask)
    if name == "nested_bubble_predictions.json":
        assert len(outputs) == 2
        assert len(build(items[:-1], width, height)) == 2  # No parent: no grouping.
        for variant in permutations(items):
            assert [r.bbox for r in build(list(variant), width, height)] == [r.bbox for r in outputs]
    else:
        assert [r.bbox for r in build(list(reversed(items)), width, height)] == [r.bbox for r in outputs]


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("dialogue", [False, True])
def test_identical_lettering_uses_identical_decision_inside_and_outside_balloons(horizontal, dialogue):
    items = synthetic()
    if not dialogue:
        items.pop()
    if horizontal:
        for item in items:
            x, y, w, h = item.bbox
            item.bbox, item.mask_origin, item.mask = [y, x, h, w], (y, x), item.mask.T
    result = build(items, 180 if horizontal else 160, 160 if horizontal else 180)
    assert len(result) == 2
    assert {tuple(r.metadata["raw_bbox"]) for r in result} == {tuple(p.bbox) for p in items[1:] if p.label == "text"}
    assert all(r.orientation == ("horizontal" if horizontal else "vertical") for r in result)


@pytest.mark.parametrize("damage", ["none", "punctuation", "empty", "solid", "misaligned", "reserved", "child_outside_crop"])
def test_same_evidence_and_safety_gates_apply_regardless_of_bubble_assignment(damage):
    items = prose_sample()
    geometry = prose_geometries(items)
    reserved = set()
    if damage == "punctuation":
        items[0].mask[45:47, 8:10] = 1
    elif damage == "empty":
        items[1].mask[:] = 0
    elif damage == "solid":
        items[1].mask[:] = 1
    elif damage == "misaligned":
        items[1].mask = np.roll(items[1].mask, 5, axis=1)
    elif damage == "reserved":
        reserved.add(1)
    elif damage == "child_outside_crop":
        # Shrink only one final child polygon: raw box/mask agreement remains
        # perfect, but child ink would be clipped by its surviving OCR crop.
        geometry[1] = (bbox_to_polygon([25, 25, 125, 12]), "horizontal", 0.)
    free = prose_groups(items, geometry=geometry, reserved=reserved)
    dialogue = prose_groups(items, geometry=geometry, reserved=reserved, bubbles=dict.fromkeys(range(3), 99))
    assert free == dialogue
    assert bool(free) == (damage == "none")


@pytest.mark.parametrize("coverage", [.5, .9, .949, .95, 1.])
def test_uncertain_bubble_relation_cannot_be_reinterpreted_as_free_text(coverage):
    items = synthetic()
    result = groups(items, coverage=dict.fromkeys(range(3), coverage))
    assert bool(result) == (coverage >= .95)


@pytest.mark.parametrize("bubbles", [{0: 3, 1: 3, 2: 4}, {0: 3, 1: 3, 2: None}])
def test_different_or_mixed_bubble_assignments_are_not_combined(bubbles):
    assert groups(synthetic(), bubbles=bubbles) == {}


def test_same_bubble_cannot_override_different_panel_ownership():
    assert groups(synthetic(), panels={0: 8, 1: 8, 2: 9}) == {}


@pytest.mark.parametrize("typography", [
    {1: ("vertical", 0.), 2: ("horizontal", 0.)},
    {1: ("vertical", 0.), 2: ("vertical", 20.)},
])
def test_different_child_directions_or_angles_are_ambiguous(typography):
    assert groups(synthetic(), typography=typography) == {}


@pytest.mark.parametrize("box", [(44, 105, 12, 60), (95, 20, 12, 80)])
def test_independent_paragraphs_remain_separate_without_an_adjacency_special_case(box):
    items = synthetic(((20, 20, 12, 80), box))
    assert groups(items) == {0: [1, 2]}  # Redundant container goes, distinct children stay.


@pytest.mark.parametrize("index", [0, 1, 2])
@pytest.mark.parametrize("damage", ["empty", "zero", "solid"])
def test_unavailable_or_solid_segmentation_never_removes_a_box(index, damage):
    items = synthetic()
    if damage == "empty":
        items[index].mask, items[index].mask_origin = np.empty((0, 0), np.uint8), (0, 0)
    else:
        items[index].mask[:] = int(damage == "solid")
    assert groups(items) == {}
    assert len(build(items)) == 3


@pytest.mark.parametrize("unique_in", ["parent", "child"])
def test_unique_lettering_not_supported_by_both_representations_is_protected(unique_in):
    items = synthetic()
    if unique_in == "parent":
        items[0].mask[25:33, 16:20] = 1
    else:
        items[0].mask[:25, :12] = 0
    assert groups(items) == {}
    assert len(build(items)) == 3


def test_single_child_requires_full_ink_coverage_just_like_multiple_children():
    items = synthetic()
    assert groups([items[0], items[1], items[-1]]) == {}  # Second column would be lost.
    items[0].mask[:, 12:] = 0
    assert groups([items[0], items[1], items[-1]]) == {0: [1]}


def test_intersecting_alternatives_stay_but_proven_nested_containers_resolve_to_original_leaves():
    items = synthetic()
    extra = deepcopy(items[1])
    extra.bbox[0] += 2
    extra.mask_origin = (extra.mask_origin[0]+2, extra.mask_origin[1])
    assert groups([*items[:-1], extra, items[-1]]) == {}
    extra = deepcopy(items[0])
    extra.bbox[3] -= 5
    extra.mask = extra.mask[:-5]
    nested = [*items[:-1], extra, items[-1]]
    assert groups(nested) == {3: [1, 2], 0: [1, 2]}
    frames = [text_geometry(items[i], 400, 400, padding=3)[0] for i in (1, 2)]
    for item in nested[:-1]:
        mask = _window(item)
        assert covered_components(mask, frames)[mask.pixels > 0].all()


def test_low_containment_user_setting_does_not_weaken_duplicate_confirmation():
    items = synthetic()
    items[-1].mask[20:35] = 0
    result = build(items, config={"containment_threshold": .5})
    assert len(result) == 3
    assert all(item.bubble_id for item in result)


@pytest.mark.parametrize("index", [0, 1, 2])
def test_every_member_requires_trusted_relation_not_only_the_parent(index):
    items = synthetic()
    confidence = dict.fromkeys(range(3), 1.)
    confidence[index] = .94
    assert groups(items, coverage=confidence) == {}
    confidence.pop(index)
    assert groups(items, coverage=confidence) == {}


def test_stray_foreground_outside_raw_boxes_cannot_justify_removing_a_parent():
    items = synthetic()
    for index, item in enumerate(items[:3]):
        large = np.zeros((180, 160), np.uint8)
        large[110:170, 20:140] = 1
        if index:
            x, y, w, h = item.bbox
            large[y:y+h, x:x+w] = item.mask
        item.mask, item.mask_origin = large, (0, 0)
    assert groups(items) == {}


@pytest.mark.parametrize("padding", [3, 50, {"top": 1, "bottom": 50, "left": 40, "right": 0}])
@pytest.mark.parametrize("dialogue", [False, True])
def test_user_padding_never_hides_unique_parent_punctuation_between_columns(padding, dialogue):
    items = synthetic()
    items[0].mask[75, 18] = 1
    if not dialogue:
        items.pop()
    result = build(items, config={"text_box_padding": padding})
    assert len(result) == 3
    assert any(r.metadata["raw_bbox"] == items[0].bbox for r in result)


@pytest.mark.parametrize("dialogue", [False, True])
def test_padding_changes_only_surviving_frames_and_never_expands_their_text_masks(dialogue):
    items = synthetic()
    if not dialogue:
        items.pop()
    normal, padded = build(items), build(items, config={"text_box_padding": 30})
    assert len(normal) == len(padded) == 2
    for first, second in zip(normal, padded, strict=True):
        assert first.bbox != second.bbox
        assert first.text_mask_origin == second.text_mask_origin
        np.testing.assert_array_equal(first.text_mask, second.text_mask)


@pytest.mark.parametrize("damage", ["different_bubble", "missing_mask", "unique_glyph", "crossed_glyph", "zero_padding"])
def test_real_jittered_dialogue_safety_guards_survive_unification(damage):
    width, height, items = recorded("nested_uneven_bubble_predictions.json")
    items = [p for p in items if p.label == "text" and 130 < p.bbox[0] < 210 and 350 < p.bbox[1] < 600]
    parent = max(range(3), key=lambda i: items[i].bbox[2]*items[i].bbox[3])
    bubbles = dict.fromkeys(range(3), 0)
    if damage == "different_bubble":
        bubbles[next(i for i in range(3) if i != parent)] = 1
    elif damage == "missing_mask":
        items[parent].mask = np.zeros_like(items[parent].mask)
    elif damage in {"unique_glyph", "crossed_glyph"}:
        p = items[parent]
        mx, my = p.mask_origin
        p.mask[450-my:451-my, 168-mx:169-mx] = 1
        if damage == "crossed_glyph":
            p.mask[450-my:451-my, 160-mx:180-mx] = 1
    geometry = {i: text_geometry(p, width, height, padding=0 if damage == "zero_padding" else 3) for i, p in enumerate(items)}
    assert covered_text_parents(items, bubbles, geometry, {}, reserved=set(), bubble_coverage=dict.fromkeys(range(3), 1.)) == {}


@pytest.mark.parametrize("horizontal", [False, True])
def test_even_one_unique_parent_pixel_outside_children_prevents_removal(horizontal):
    items = synthetic()
    items[0].mask[75, 18] = 1
    if horizontal:
        for item in items:
            x, y, w, h = item.bbox
            item.bbox, item.mask_origin, item.mask = [y, x, h, w], (y, x), item.mask.T
    outputs = build(items, 180 if horizontal else 160, 160 if horizontal else 180)
    assert len(outputs) == 3
    parent = next(r for r in outputs if r.metadata["raw_bbox"] == items[0].bbox)
    np.testing.assert_array_equal(parent.text_mask, _window(items[0]).pixels)


def test_dialogue_context_is_preserved_without_keeping_a_duplicate_parent():
    width, height, items = recorded()
    outputs = build(items, width, height)
    rows = [SimpleNamespace(region_key=f"R{i+1:03d}", source_text=f"column {i}", translated_text="",
                            reading_order=i+1, panel_id=None, bubble_id=r.bubble_id, bbox=r.bbox, orientation=r.orientation)
            for i, r in enumerate(outputs)]
    context = page_context(rows)
    assert [r["id"] for r in context] == ["R001", "R002"]
    assert context[0]["bubble_id"] == context[1]["bubble_id"] is not None
    assert [r["source"] for r in context] == ["column 0", "column 1"]


@pytest.mark.parametrize("protection", ["manual", "locked", "legacy"])
@pytest.mark.parametrize("target", ["right", "left", "parent", "elsewhere"])
def test_redetection_replaces_every_frame_and_recovers_all_columns(client, monkeypatch, protection, target):
    width, height, items = recorded()
    right, left = columns = build(items, width, height)
    with patch("app.services.detection.layout.covered_text_parents", return_value={}):
        parent = next(r for r in build(items, width, height) if r.metadata["raw_bbox"] == items[-1].bbox)
    chosen = {"right": right, "left": left, "parent": parent, "elsewhere": right}[target]
    polygon = chosen.polygon if target != "elsewhere" else bbox_to_polygon([300, 10, 10, 40])
    bbox = chosen.bbox if target != "elsewhere" else [300, 10, 10, 40]
    image = BytesIO()
    Image.new("RGB", (width, height), "white").save(image, "PNG")
    project = client.post("/api/projects", json={"name": "unified-manual-protection"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("page.png", image.getvalue(), "image/png")}).json()[0]
    detector = RFDetrTextDetector({"device": "cpu"})
    detector._text_classifier.ensure_loaded = Mock()
    detector._text_classifier.detect_layout = Mock(return_value=(width, height, items))
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda *_: (detector, "rfdetr"))
    with SessionLocal() as db:
        manual = TextRegion(image_id=page["id"], region_key="R001", reading_order=1,
                            polygon=polygon, bbox=bbox, translated_polygon=polygon, translated_bbox=bbox,
                            source_text="user source", translated_text="user translation", rotation=7.,
                            locked=protection == "locked", layout_data={"manual": True} if protection == "manual" else
                            {"detection": {"detector": "rfdetr"}} if protection == "locked" else {})
        db.add(manual)
        db.commit()
        stored = db.get(ImagePage, page["id"])
        for _ in range(2):
            previous_ids = {r.id for r in stored.regions}
            PipelineProcessor(db).detect(stored, None, force=True)
            db.commit()
            db.refresh(stored)
            assert_rebuilt_regions(stored.regions, columns, previous_ids)
    response = client.get(f"/api/images/{page['id']}/regions")
    assert response.status_code == 200 and "duplicate_members" not in response.text
    detector.unload()

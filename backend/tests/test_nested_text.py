from __future__ import annotations

import json
from copy import deepcopy
from io import BytesIO
from itertools import permutations
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from app.core.database import SessionLocal
from app.models import ImagePage, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services.detection.layout import RFDetrTextDetector, build_layout_regions
from app.services.detection.layout_protocol import decode_predictions
from app.services.detection.mask_geometry import covered_components
from app.services.detection.nested_text import _Mask, nested_text_duplicates
from app.services.detection.preserved_text import exclude_preserved_text, protected_duplicate_candidates
from app.services.detection.text_roles import LayoutPrediction
from PIL import Image


def real_predictions():
    # Recorded RF-DETR output for the reported two-column dialogue. Packed
    # binary masks/boxes only; no original page or OCR text is needed by tests.
    return decode_predictions(json.loads((Path(__file__).parent / "fixtures/nested_bubble_predictions.json").read_text()))


def build(items, width=160, height=180):
    return build_layout_regions(items, width, height, {}, page_key="nested-test")


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


def groups(items, bubbles=None, typography=None):
    indices = [i for i, item in enumerate(items) if item.label == "text"]
    return nested_text_duplicates(items, bubbles or dict.fromkeys(indices, len(items)-1),
                                  typography or dict.fromkeys(indices, ("vertical", 0.)))


def test_real_nested_columns_become_one_complete_frame_without_losing_geometry_or_mutating_inputs():
    width, height, items = real_predictions()
    original = deepcopy(items)
    result, = build(items, width, height)
    assert result.metadata["raw_bbox"] == items[-1].bbox
    assert result.confidence == items[-1].confidence  # Coverage, not highest score, decides.
    assert result.orientation == "vertical"
    assert result.metadata["nested_deduplication"]["removed_count"] == 2
    assert result.metadata["text_box_padding"] == {"top": 3, "bottom": 3, "left": 3, "right": 3}
    assert result.bbox == pytest.approx([items[-1].bbox[0]-3, items[-1].bbox[1]-3,
                                       items[-1].bbox[2]+6, items[-1].bbox[3]+6])
    assert len(result.duplicate_members) == 2
    assert all(child.bubble_id == result.bubble_id and not child.duplicate_members for child in result.duplicate_members)
    for a, b in zip(items, original, strict=True):
        assert a.bbox == b.bbox and a.mask_origin == b.mask_origin
        np.testing.assert_array_equal(a.mask, b.mask)
    # Without an enclosing detection, two independent columns stay independent.
    assert len(build(items[:-1], width, height)) == 2


def test_real_nested_deduplication_is_independent_of_prediction_order():
    width, height, items = real_predictions()
    for variant in permutations(items):
        result, = build(list(variant), width, height)
        assert result.metadata["raw_bbox"] == items[-1].bbox
        assert {tuple(child.metadata["raw_bbox"]) for child in result.duplicate_members} == {tuple(i.bbox) for i in items[1:3]}


@pytest.mark.parametrize("horizontal", [False, True])
def test_verified_vertical_columns_and_horizontal_lines_can_deduplicate(horizontal):
    items = synthetic()
    if horizontal:
        for item in items:
            x, y, w, h = item.bbox
            item.bbox, item.mask_origin, item.mask = [y, x, h, w], (y, x), item.mask.T
    result, = build(items, 180 if horizontal else 160, 160 if horizontal else 180)
    assert result.metadata["raw_bbox"] == items[0].bbox
    assert result.orientation == ("horizontal" if horizontal else "vertical")


@pytest.mark.parametrize("bubbles", [{0: 3, 1: 3, 2: 4}, {0: None, 1: None, 2: None}, {0: 3, 1: 3, 2: None}])
def test_different_or_missing_bubble_assignments_never_merge(bubbles):
    assert groups(synthetic(), bubbles) == {}


@pytest.mark.parametrize("typography", [
    {0: ("vertical", 0.), 1: ("vertical", 0.), 2: ("horizontal", 0.)},
    {0: ("vertical", 0.), 1: ("vertical", 0.), 2: ("vertical", 20.)},
])
def test_different_writing_directions_or_angles_stay_separate(typography):
    assert groups(synthetic(), typography=typography) == {}


@pytest.mark.parametrize("box", [(44, 105, 12, 60), (95, 20, 12, 80)])
def test_independent_paragraphs_and_distant_asides_stay_separate(box):
    assert groups(synthetic(((20, 20, 12, 80), box))) == {}


@pytest.mark.parametrize("index", [0, 1, 2])
def test_empty_or_failed_segmentation_is_not_enough_evidence_to_remove_a_box(index):
    items = synthetic()
    items[index].mask, items[index].mask_origin = np.empty((0, 0), np.uint8), (0, 0)
    assert groups(items) == {}
    assert len(build(items)) == 3


@pytest.mark.parametrize("unique_in", ["parent", "child"])
def test_unique_lettering_in_either_representation_prevents_suppression(unique_in):
    items = synthetic()
    if unique_in == "parent":
        items[0].mask[25:33, 16:20] = 1  # New ink between columns, not edge noise.
    else:
        items[0].mask[:25, :12] = 0  # A child contains characters the parent missed.
    assert groups(items) == {}
    assert len(build(items)) == 3


def test_stray_foreground_outside_a_box_cannot_supply_missing_coverage():
    items = synthetic()
    # All masks have the same, large stray island elsewhere on the page, while
    # the parent has no lettering at all inside its actual detection window.
    for index, item in enumerate(items[:3]):
        large = np.zeros((180, 160), np.uint8)
        large[110:170, 20:140] = 1
        if index:
            x, y, w, h = item.bbox
            large[y:y+h, x:x+w] = item.mask
        item.mask, item.mask_origin = large, (0, 0)
    assert groups(items) == {}


def test_single_containment_and_intersecting_chains_are_not_grouped():
    items = synthetic()
    assert groups([items[0], items[1], items[-1]]) == {}
    extra = deepcopy(items[1])
    extra.bbox[0] += 2
    extra.mask_origin = (extra.mask_origin[0]+2, extra.mask_origin[1])
    assert groups([*items[:-1], extra, items[-1]]) == {}


def test_low_containment_user_setting_does_not_weaken_duplicate_confirmation():
    items = synthetic()
    items[-1].mask[20:35] = 0  # .5 assignment is enabled, but not trustworthy for removal.
    result = build_layout_regions(items, 160, 180, {"containment_threshold": .5}, page_key="loose")
    assert len(result) == 3
    assert all(item.bubble_id for item in result)


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("unique_ink", [False, True])
def test_protected_column_fallback_keeps_every_parent_ink_component(horizontal, unique_ink):
    items = synthetic()
    if unique_ink:
        # Below the aggregate dedup tolerance, but outside both child crops.
        items[0].mask[75, 18] = 1
    if horizontal:
        for item in items:
            x, y, w, h = item.bbox
            item.bbox, item.mask_origin, item.mask = [y, x, h, w], (y, x), item.mask.T
    parent, = build(items, 180 if horizontal else 160, 160 if horizontal else 180)
    assert len(parent.duplicate_members) == 2
    child = parent.duplicate_members[0]
    protected = SimpleNamespace(polygon=child.polygon, bbox=child.bbox)
    alternatives = protected_duplicate_candidates(parent, [protected])
    assert alternatives == ([parent] if unique_ink else parent.duplicate_members)
    retained = [piece for item in alternatives for piece in exclude_preserved_text(item, [protected])]
    polygons = [protected.polygon, *(piece.polygon for piece in retained)]
    mask = _Mask(parent.text_mask, *parent.text_mask_origin)
    assert covered_components(mask, polygons)[mask.pixels > 0].all()


@pytest.mark.parametrize("protection", ["manual", "locked", "legacy"])
@pytest.mark.parametrize("target", ["right", "left", "parent", "elsewhere"])
def test_forced_redetection_preserves_user_frames_and_does_not_lose_the_other_column(client, monkeypatch, protection, target):
    width, height, items = real_predictions()
    parent, = build(items, width, height)
    right, left = parent.duplicate_members
    chosen = {"right": right, "left": left, "parent": parent, "elsewhere": right}[target]
    polygon = chosen.polygon if target != "elsewhere" else [[300, 10], [310, 10], [310, 50], [300, 50]]
    bbox = chosen.bbox if target != "elsewhere" else [300, 10, 10, 40]
    image = BytesIO()
    Image.new("RGB", (width, height), "white").save(image, "PNG")
    project = client.post("/api/projects", json={"name": "nested-manual-protection"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("page.png", image.getvalue(), "image/png")}).json()[0]
    detector = RFDetrTextDetector({"device": "cpu"})
    detector._text_classifier.ensure_loaded = Mock()
    detector._text_classifier.detect_layout = Mock(return_value=(width, height, items))
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda *_: (detector, "rfdetr"))
    with SessionLocal() as db:
        manual = TextRegion(image_id=page["id"], region_key="R001", reading_order=1,
                            polygon=polygon, bbox=bbox, translated_polygon=polygon, translated_bbox=bbox,
                            source_text="user source", translated_text="user translation", rotation=7.,
                            locked=protection == "locked",
                            layout_data={"manual": True} if protection == "manual" else
                            {"detection": {"detector": "rfdetr", "layout_version": 5}} if protection == "locked" else {})
        db.add(manual)
        db.commit()
        original_id = manual.id
        stored = db.get(ImagePage, page["id"])
        for _ in range(2):  # Repeated detection must not accumulate duplicates.
            PipelineProcessor(db).detect(stored, None, force=True)
            db.commit()
            db.refresh(stored)
            protected = next(r for r in stored.regions if r.id == original_id)
            assert protected.polygon == polygon and protected.rotation == 7.
            assert protected.source_text == "user source" and protected.translated_text == "user translation"
            others = [r for r in stored.regions if r.id != original_id]
            if target == "parent":
                assert not others
            else:
                assert len(others) == 1
                expected = {"right": left, "left": right, "elsewhere": parent}[target]
                assert others[0].bbox == expected.bbox
                assert others[0].layout_data["detection"]["raw_bbox"] == expected.metadata["raw_bbox"]
        # Transient alternatives contain numpy-backed geometry and must never
        # become stored JSON or appear in the page/region API.
    response = client.get(f"/api/images/{page['id']}/regions")
    assert response.status_code == 200 and "duplicate_members" not in response.text
    detector.unload()

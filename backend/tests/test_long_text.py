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
from app.services.detection.layout import RFDetrTextDetector, build_layout_regions
from app.services.detection.layout_protocol import decode_predictions
from app.services.detection.long_text import long_text_partitions
from app.services.detection.nested_text import _coverage, _Mask, _window
from app.services.detection.text_roles import LayoutPrediction
from app.utils.geometry import bbox_to_polygon, intersection_area
from PIL import Image


def recorded():
    return decode_predictions(json.loads((Path(__file__).parent / "fixtures/long_text_predictions.json").read_text()))


def build(items, width=400, height=400):
    return build_layout_regions(items, width, height, {}, page_key="long-text-test")


def sample():
    mask = np.zeros((180, 240), np.uint8)
    for y in range(5, 165, 20):
        for x in range(5, 225, 12):
            mask[y:y+10, x:x+7] = 1
    parent = LayoutPrediction("text", .3, [20, 20, 240, 180], mask, (20, 20))
    children = [LayoutPrediction("text", .8, [20, 20+y, 240, 40], mask[y:y+40].copy(), (20, 20+y))
                for y in (0, 40, 120)]  # The two lines at 80..120 are only in the parent.
    return [parent, *children]


def partitions(items, *, bubbles=None, typography=None, panels=None):
    indices = [i for i, p in enumerate(items) if p.label == "text"]
    return long_text_partitions(items, bubbles or dict.fromkeys(indices),
                                typography or dict.fromkeys(indices, ("horizontal", 0.)), panels or dict.fromkeys(indices))


def signature(results):
    return [(r.bbox, r.polygon, r.confidence, r.orientation, r.bubble_id, r.metadata) for r in results]


def test_recorded_nested_prose_preserves_all_16_lines_without_overlapping_frames():
    width, height, raw = recorded()
    untouched = deepcopy(raw)
    results = build(raw, width, height)
    assert len(results) == 7
    pieces = [r for r in results if "long_text_partition" in r.metadata]
    assert [r.metadata["long_text_partition"]["line_count"] for r in pieces] == [4, 4, 2, 2, 3, 1]
    assert all(r.orientation == "horizontal" and r.metadata["text_angle"] == 0 for r in pieces)
    assert len(results[0].duplicate_members) == 0  # Title is untouched.
    for i, result in enumerate(results):
        assert all(intersection_area(result.bbox, other.bbox) == 0 for other in results[i+1:])
    frames = [_Mask(np.ones((int(r.bbox[3]), int(r.bbox[2])), np.uint8), int(r.bbox[0]), int(r.bbox[1])) for r in pieces]
    parent = max(raw, key=lambda p: p.bbox[2]*p.bbox[3])
    assert _coverage(_window(parent), frames) == 1
    assert any(r.bbox[1] <= 224 and r.bbox[1]+r.bbox[3] >= 252 for r in pieces)  # Previously missed first two lines.
    for a, b in zip(raw, untouched, strict=True):
        assert a.bbox == b.bbox and a.mask_origin == b.mask_origin
        np.testing.assert_array_equal(a.mask, b.mask)


def test_prediction_order_does_not_change_partition_geometry_or_reading_order():
    width, height, raw = recorded()
    expected = signature(build(raw, width, height))
    rng = np.random.default_rng(123)
    for _ in range(12):
        assert signature(build([raw[i] for i in rng.permutation(len(raw))], width, height)) == expected


@pytest.mark.parametrize("padding", [0, 8, 50, {"top": 50, "bottom": 0, "left": 7, "right": 40},
                                   {"top": 0, "bottom": 50, "left": 40, "right": 7}])
def test_configured_padding_applies_to_prose_and_keeps_separate_paragraphs(padding):
    width, height, raw = recorded()
    results = build_layout_regions(raw, width, height, {"text_box_padding": padding}, page_key="prose-padding")
    baseline = build(raw, width, height)
    assert len(results) == len(baseline)
    margins = padding if isinstance(padding, dict) else dict.fromkeys(("top", "bottom", "left", "right"), padding)
    for result, original in zip(results, baseline, strict=True):
        assert result.orientation == original.orientation
        assert result.metadata.get("long_text_partition") == original.metadata.get("long_text_partition")
        assert result.metadata["text_box_padding"] == margins
        if "long_text_partition" in result.metadata:
            assert result.bbox[0] == max(0, result.metadata["raw_bbox"][0]-margins["left"])
            assert all(intersection_area(result.bbox, other.bbox) == 0 for other in results
                       if other is not result and "long_text_partition" in other.metadata)


def test_unique_parent_lines_are_recovered_instead_of_deleting_the_enclosing_box():
    partition, = partitions(sample()).values()
    assert [len(piece.lines) for piece in partition.pieces] == [2, 2, 2, 2]
    assert partition.pieces[2].owner == 0  # No child supplied these lines.
    assert sum(len(piece.lines) for piece in partition.pieces) == 8


@pytest.mark.parametrize("change", ["vertical", "slanted", "mixed", "bubble", "different_panel", "short",
                                   "empty_parent", "empty_child", "unique_child", "punctuation", "bridge", "solid_mask"])
def test_ambiguous_or_out_of_scope_layout_is_left_untouched(change):
    items = sample()
    typography = dict.fromkeys(range(4), ("horizontal", 0.))
    bubbles = dict.fromkeys(range(4))
    panels = dict.fromkeys(range(4))
    if change == "vertical":
        typography = dict.fromkeys(range(4), ("vertical", 0.))
    elif change == "slanted":
        typography = dict.fromkeys(range(4), ("horizontal", 12.))
    elif change == "mixed":
        typography[1] = ("vertical", 0.)
    elif change == "bubble":
        bubbles[1] = 99
    elif change == "different_panel":
        panels[1] = 99
    elif change == "short":
        items[0].mask[100:] = 0
        items.pop()
        typography.pop(3)
    elif change == "empty_parent":
        items[0].mask[:] = 0
    elif change == "empty_child":
        items[1].mask[:] = 0
    elif change == "unique_child":
        items[1].mask[15:19, 10:90] = 1
    elif change == "punctuation":
        items[0].mask[97:100, 1:3] = 1  # Independent tiny line, not merely an antialiasing edge.
    elif change == "bridge":
        items[0].mask[15:25, 5] = 1  # Faint connected strokes cannot count as two separate lines.
    elif change == "solid_mask":
        items[0].mask[:] = 1
    assert partitions(items, typography=typography, bubbles=bubbles, panels=panels) == {}


@pytest.mark.parametrize("label", ["bubble", "panel"])
def test_enclosing_frame_straddling_dialogues_or_panels_never_uses_prose_rule(label):
    items = sample()
    items.append(LayoutPrediction(label, .99, [0, 0, 120, 400], np.ones((400, 120), np.uint8), (0, 0)))
    assert partitions(items) == {}


def test_a_single_containing_panel_is_allowed_but_not_multiple_columns():
    items = sample()
    items.append(LayoutPrediction("panel", .99, [0, 0, 400, 400], np.ones((400, 400), np.uint8), (0, 0)))
    assert partitions(items, panels=dict.fromkeys(range(4), 4))
    parent = items[0]
    columns = [LayoutPrediction("text", .8, [20+x, 20, 120, 180], parent.mask[:, x:x+120], (20+x, 20)) for x in (0, 120)]
    assert partitions([parent, *columns]) == {}


def test_unequal_columns_with_nested_paragraphs_are_not_combined_into_lines():
    items = sample()
    for item in items:
        item.mask[:, 150:180] = 0  # The wider column could exceed the 60% ownership threshold.
    assert partitions(items) == {}


@pytest.mark.parametrize("label", ["text", "onomatopoeia"])
def test_intersecting_noncontained_candidate_aborts_partition(label):
    items = sample()
    items.append(LayoutPrediction(label, .8, [0, 70, 60, 100], np.ones((100, 60), np.uint8), (0, 70)))
    assert partitions(items) == {}


def test_existing_bubble_and_angle_fixtures_are_bit_identical_with_rule_enabled_or_disabled():
    for name in ("nested_bubble_predictions.json", "upright_text_predictions.json"):
        width, height, raw = decode_predictions(json.loads((Path(__file__).parent / "fixtures" / name).read_text()))
        enabled = signature(build(raw, width, height))
        with patch("app.services.detection.layout.long_text_partitions", return_value={}):
            assert signature(build(raw, width, height)) == enabled


@pytest.mark.parametrize("protection", ["manual", "locked", "legacy"])
@pytest.mark.parametrize("scope", ["line", "paragraph", "whole"])
def test_redetection_preserves_user_frames_and_uncovered_lines(client, monkeypatch, protection, scope):
    width, height, raw = recorded()
    pieces = build(raw, width, height)
    chosen = pieces[1].duplicate_members[0] if scope == "line" else pieces[1]
    bbox = chosen.bbox if scope != "whole" else [49, 63, 422, 314]
    polygon = bbox_to_polygon(bbox)
    image = BytesIO()
    Image.new("RGB", (width, height), "white").save(image, "PNG")
    project = client.post("/api/projects", json={"name": "long-text-protection"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("page.png", image.getvalue(), "image/png")}).json()[0]
    detector = RFDetrTextDetector({"device": "cpu"})
    detector._text_classifier.ensure_loaded = Mock()
    detector._text_classifier.detect_layout = Mock(return_value=(width, height, raw))
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda *_: (detector, "rfdetr"))
    with SessionLocal() as db:
        region = TextRegion(image_id=page["id"], region_key="R001", reading_order=1,
            bbox=bbox, polygon=polygon, translated_bbox=bbox, translated_polygon=polygon,
            source_text="user source", translated_text="user translation", rotation=9., locked=protection == "locked",
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
            assert protected.source_text == "user source" and protected.translated_text == "user translation"
            assert protected.bbox == bbox and protected.rotation == 9.
            others = [r for r in stored.regions if r.id != identifier]
            assert len(others) == {"line": 9, "paragraph": 6, "whole": 1}[scope]
            if scope == "line":
                assert all(any(r.bbox == line.bbox for r in others) for line in pieces[1].duplicate_members[1:])
    detector.unload()

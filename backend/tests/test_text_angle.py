from __future__ import annotations

import json
import math
from pathlib import Path

import cv2
import numpy as np
import pytest
from app.services.detection.layout import build_layout_regions, text_geometry
from app.services.detection.layout_protocol import decode_predictions
from app.services.detection.text_angle import projection_score, supported_text_angle
from app.services.detection.text_roles import LayoutPrediction
from app.utils.geometry import bbox_to_polygon


def recorded_predictions():
    # Packed model output from the reported upright dialogue; no original page
    # or OCR text is required to reproduce the old 18-degree false rotation.
    path = Path(__file__).parent / "fixtures/upright_text_predictions.json"
    return decode_predictions(json.loads(path.read_text()))


def crop(item, margin=2):
    bx, by, bw, bh = item.bbox
    mx, my = item.mask_origin
    mh, mw = item.mask.shape
    left, top = max(mx, math.floor(bx-margin)), max(my, math.floor(by-margin))
    right, bottom = min(mx+mw, math.ceil(bx+bw+margin)), min(my+mh, math.ceil(by+bh+margin))
    return item.mask[top-my:bottom-my, left-mx:right-mx]


def mask_prediction(mask):
    rows, cols = np.nonzero(mask)
    bbox = [int(cols.min()), int(rows.min()), int(cols.max()-cols.min()+1), int(rows.max()-rows.min()+1)]
    return LayoutPrediction("text", .95, bbox, mask, (0, 0))


def raw_score(x, y, angle, vertical):
    rad = math.radians(angle)
    return projection_score(x*math.cos(rad)+y*math.sin(rad) if vertical else y*math.cos(rad)-x*math.sin(rad))


def test_real_upright_dialogue_no_longer_rotates_from_uneven_columns_and_punctuation():
    width, height, items = recorded_predictions()
    original = items[-1]
    y, x = np.nonzero(crop(original))
    x, y = x.astype(float), y.astype(float)
    # Establish that this exact model output triggered the original algorithm.
    old_angle = max(np.arange(-45., 45.01, .5), key=lambda a: raw_score(x, y, a, True))
    assert old_angle == 18.
    assert raw_score(x, y, old_angle, True) > raw_score(x, y, 0., True)
    polygon, direction, angle = text_geometry(original, width, height)
    assert (direction, angle) == ("vertical", 0.)
    assert polygon == bbox_to_polygon(original.bbox)
    results = build_layout_regions(items, width, height, {}, page_key="upright")
    assert len(results) == 2  # No side effect on independent dialogue regions.
    for result in results:
        assert result.metadata["text_angle"] == 0.
        x, y, w, h = result.metadata["raw_bbox"]
        assert result.polygon == bbox_to_polygon([x-3, y-3, w+6, h+6])
        assert result.bubble_id is not None


@pytest.mark.parametrize("horizontal", [False, True])
def test_upright_guard_also_handles_transposed_writing_and_free_text(horizontal):
    _, _, items = recorded_predictions()
    mask = crop(items[-1])
    if horizontal:
        mask = mask.T.copy()
    mask = np.pad(mask, 10)
    item = mask_prediction(mask)
    h, w = mask.shape
    result, = build_layout_regions([item], w, h, {}, page_key="free-upright")
    assert result.orientation == ("horizontal" if horizontal else "vertical")
    assert result.metadata["text_angle"] == 0.
    assert result.bubble_id is None


@pytest.mark.parametrize("angle", [-25, -12, -7, 7, 12, 25])
@pytest.mark.parametrize("vertical", [False, True])
def test_clear_slanted_multiline_glyphs_keep_their_real_angle(vertical, angle):
    mask = np.zeros((180, 220), np.uint8)
    for y in (50, 75, 100):
        for x in range(45, 174, 16):
            mask[y:y+9, x:x+8] = 1
    if vertical:
        mask = mask.T.copy()
    h, w = mask.shape
    mask = cv2.warpAffine(mask, cv2.getRotationMatrix2D((w/2, h/2), -angle, 1), (w, h), flags=cv2.INTER_NEAREST)
    item = mask_prediction(mask)
    polygon, direction, detected = text_geometry(item, w, h)
    assert direction == ("vertical" if vertical else "horizontal")
    assert detected == pytest.approx(angle, abs=.5)
    assert (polygon[1][1]-polygon[0][1])*angle > 0


@pytest.mark.parametrize("angle", [-20, -12, 12, 20])
def test_clear_rotation_of_real_lettering_is_not_disabled(angle):
    _, _, items = recorded_predictions()
    mask = np.pad(crop(items[1], margin=0), 40)
    h, w = mask.shape
    mask = cv2.warpAffine(mask, cv2.getRotationMatrix2D((w/2, h/2), -angle, 1), (w, h), flags=cv2.INTER_NEAREST)
    _, direction, detected = text_geometry(mask_prediction(mask), w, h)
    assert direction == "vertical"
    assert detected == pytest.approx(angle, abs=1.5)


def test_a_strong_global_score_is_insufficient_when_local_lines_are_upright():
    # Two individually vertical line segments with staggered column starts.
    mask = np.zeros((120, 60), np.uint8)
    mask[5:17, 10:14] = 1
    mask[25:37, 20:24] = 1
    y, x = np.nonzero(mask)
    x, y = x.astype(float), y.astype(float)
    best = max(np.arange(-45., 45.01, .5), key=lambda a: raw_score(x, y, a, True))
    assert abs(best) >= 3
    assert raw_score(x, y, best, True) > 1.1*raw_score(x, y, 0., True)
    assert supported_text_angle(x, y, True, best) == 0.


@pytest.mark.parametrize("count", [0, 4, 12, 23])
def test_sparse_dots_do_not_provide_enough_evidence_for_rotation(count):
    x, y = np.arange(count, dtype=float), np.arange(count, dtype=float)
    assert supported_text_angle(x, y, True, -30.) == 0.

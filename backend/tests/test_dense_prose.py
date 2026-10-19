"""Actual RF-DETR failures: dense nested horizontal/vertical containers."""
import json
import math
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest
from app.services.detection.layout import build_layout_regions
from app.services.detection.layout_protocol import decode_predictions
from app.services.detection.mask_evidence import _coverage, _Mask, _window
from app.services.detection.mask_geometry import covered_components
from app.utils.geometry import bbox_to_polygon


def recorded(name):
    value = json.loads((Path(__file__).parent/'fixtures'/name).read_text())
    width, height, predictions = decode_predictions(value)
    return width, height, predictions, value['config']


@pytest.mark.parametrize('name,direction', [
    ('dense_vertical_prose_predictions.json', 'vertical'),
    ('dense_nested_prose_predictions.json', 'horizontal'),
])
@pytest.mark.parametrize('padding', [0, 3, {'top': 0, 'bottom': 8, 'left': 2, 'right': 12}])
def test_real_dense_containers_preserve_all_ink_without_enclosing_duplicates(name, direction, padding):
    width, height, raw, config = recorded(name)
    initial = deepcopy(raw)
    config['text_box_padding'] = padding
    results = build_layout_regions(raw, width, height, config, page_key=name)
    results = [r for r in results if not r.metadata['text_classification']['excluded']]
    prose = [r for r in results if r.metadata.get('long_text_partition')]
    assert len(prose) >= 4
    assert all(r.orientation == direction for r in prose)
    assert all(r.metadata['long_text_partition']['method'] == 'verified_prose_gutters' for r in prose)
    masks = [_Mask(r.text_mask, *r.text_mask_origin) for r in results]
    # Unmodified fractional detector frames use floor/ceil raster crops for
    # OCR, which can include edge pixel centres outside their float polygon.
    # Newly partitioned prose has integer ink bounds and must fit exactly.
    frames = [r.polygon if r.metadata.get('long_text_partition') else bbox_to_polygon([
        math.floor(r.bbox[0]), math.floor(r.bbox[1]),
        math.ceil(r.bbox[0]+r.bbox[2])-math.floor(r.bbox[0]),
        math.ceil(r.bbox[1]+r.bbox[3])-math.floor(r.bbox[1])]) for r in results]
    for p in raw:
        if p.label == 'text' and p.confidence > config['text_threshold']:
            ink = _window(p)
            assert _coverage(ink, masks) == 1.
            assert covered_components(ink, frames)[ink.pixels > 0].all()
    for i,a in enumerate(results):
        x,y,w,h = a.bbox
        for b in results[i+1:]:
            left,top,bw,bh = b.bbox
            overlap = max(0,min(x+w,left+bw)-max(x,left))*max(0,min(y+h,top+bh)-max(y,top))
            assert overlap == 0
    if direction == 'vertical':
        assert [r.bbox[0] for r in prose] == sorted((r.bbox[0] for r in prose), reverse=True)
    for actual, original in zip(raw, initial, strict=True):
        assert actual.bbox == original.bbox
        np.testing.assert_array_equal(actual.mask, original.mask)


def test_real_near_duplicate_credit_still_resolves_with_iou_evidence():
    width, height, raw, config = recorded('credits_near_duplicate_predictions.json')
    results = build_layout_regions(raw, width, height, config, page_key='credits')
    assert sum(bool(r.metadata.get('iou_deduplication')) for r in results) == 1

"""Survivor/evidence invariants, not just a desired number of boxes."""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from app.services.detection.covered_text import covered_text_parents, duplicate_text_instances
from app.services.detection.layout import build_layout_regions
from app.services.detection.layout_protocol import decode_predictions
from app.services.detection.mask_evidence import _dilate, _Mask, _union_masks, _window
from app.services.detection.mask_geometry import components_supported, covered_components
from app.services.detection.text_roles import LayoutPrediction
from app.utils.geometry import bbox_to_polygon


def leaf(x, y=20, *, thin=False):
    pixels = np.zeros((100, 18), np.uint8)
    for row in range(6, 90, 14):
        pixels[row:row+8, 4:14] = 1
        if thin:
            pixels[row+1:row+7, 5:13] = 0
    return LayoutPrediction('text', .8, [x, y, 18, 100], pixels, (x, y))


def container(items, margin=4):
    ink = _union_masks([_window(p) for p in items])
    h, w = ink.pixels.shape
    return LayoutPrediction('text', .6, [ink.x-margin, ink.y-margin, w+2*margin, h+2*margin],
                            np.pad(ink.pixels, margin), (ink.x-margin, ink.y-margin))


def geometry(items, padding=3, horizontal=False):
    return {i: (bbox_to_polygon([p.bbox[0]-padding, p.bbox[1]-padding,
                                p.bbox[2]+2*padding, p.bbox[3]+2*padding]),
                'horizontal' if horizontal else 'vertical', 0.) for i, p in enumerate(items)}


def resolve(items, *, horizontal=False, dialogue=False, padding=3):
    geo = geometry(items, padding, horizontal)
    owners, panels = dict.fromkeys(geo, 999 if dialogue else None), dict.fromkeys(geo)
    confidence = dict.fromkeys(geo, 1.)
    # Exercise both production duplicate passes, including identical query piles.
    exact = duplicate_text_instances(items, owners, geo, panels, bubble_coverage=confidence, include_fragments=False)
    geo = {i: g for i, g in geo.items() if i not in exact}
    provenance = {}
    duplicates = duplicate_text_instances(items, owners, geo, panels, bubble_coverage=confidence, replacements=provenance)
    groups = covered_text_parents(items, owners, geo, panels, reserved=set(), bubble_coverage=confidence,
                                  ignored=duplicates | exact, replacements=provenance)
    removed = exact | duplicates | groups.keys()
    kept = [i for i in geo if i not in removed]
    assert all(child in kept for children in groups.values() for child in children)
    # Every ORIGINAL connected stroke must fit one final crop, not two partial
    # crops or a query that has itself subsequently been removed.
    ink = _union_masks([_window(p) for p in items])
    assert covered_components(ink, [geo[i][0] for i in kept])[ink.pixels > 0].all()
    return [items[i] for i in kept]


@pytest.mark.parametrize('dialogue', [False, True])
@pytest.mark.parametrize('horizontal', [False, True])
@pytest.mark.parametrize('thin', [False, True])
@pytest.mark.parametrize('kind', ['one_tight', 'one_fragment', 'two_leaves', 'many_leaves',
                                  'nested', 'duplicate_pile', 'nested_pile'])
def test_containment_matrix_preserves_original_ink_and_complete_leaf_crops(kind, thin, horizontal, dialogue):
    leaves = [leaf(20+40*i, thin=thin) for i in range(4)]
    if kind == 'one_tight':
        items, expected = [container(leaves[:1]), leaves[0]], leaves[:1]
    elif kind == 'one_fragment':
        full = container(leaves[:2])
        items, expected = [full, leaves[0]], [full]
    else:
        leaves = leaves[:2] if kind == 'two_leaves' else leaves
        items, expected = [container(leaves, 8), *leaves], leaves
        if kind in {'nested', 'nested_pile'}:
            items += [container(leaves[:2]), container(leaves[2:])]
        if kind in {'duplicate_pile', 'nested_pile'}:
            items = [deepcopy(p) for p in items for _ in range(4)]
    expected_boxes = {tuple(p.bbox) for p in expected}
    if horizontal:
        expected_boxes = {(y, x, h, w) for x, y, w, h in expected_boxes}
        for p in items:
            x, y, w, h = p.bbox
            p.bbox, p.mask_origin, p.mask = [y, x, h, w], tuple(reversed(p.mask_origin)), p.mask.T.copy()
    before = deepcopy(items)
    for seed in range(5):
        rng = np.random.default_rng(seed)
        # Confidence and input order must not decide whether unique text exists.
        variant = deepcopy(items)
        for p in variant:
            p.confidence = float(rng.uniform(.26, .99))
        variant = [variant[i] for i in rng.permutation(len(variant))]
        kept = resolve(variant, horizontal=horizontal, dialogue=dialogue)
        assert {tuple(p.bbox) for p in kept} == expected_boxes
        assert len(kept) == len(expected_boxes)
    for a, b in zip(items, before, strict=True):
        assert a.bbox == b.bbox and a.mask_origin == b.mask_origin
        np.testing.assert_array_equal(a.mask, b.mask)


def recorded(name):
    return decode_predictions(json.loads((Path(__file__).parent/'fixtures'/name).read_text()))


@pytest.mark.parametrize('padding', [0, 3, 6, 50, {'top': 0, 'bottom': 6, 'left': 3, 'right': 0}])
def test_reported_three_column_bubble_removes_only_redundant_parent(padding):
    w, h, raw = recorded('nested_edge_variation_predictions.json')
    config = {'text_box_padding': padding}
    before = deepcopy(raw)
    with patch('app.services.detection.layout.covered_text_parents', return_value={}):
        baseline = build_layout_regions(raw, w, h, config, page_key='edge-variation')
    output = build_layout_regions(raw, w, h, config, page_key='edge-variation')
    parent = next(p for p in raw if 535 < p.bbox[0] < 536 and 102 < p.bbox[2] < 103)
    assert len(baseline) == len(output)+1
    assert not any(r.metadata['raw_bbox'] == parent.bbox for r in output)
    for result in output:
        previous = next(r for r in baseline if r.metadata['raw_bbox'] == result.metadata['raw_bbox']
                        and r.metadata['text_classification']['label'] == result.metadata['text_classification']['label'])
        assert (result.bbox, result.polygon, result.orientation, result.bubble_id) == (
            previous.bbox, previous.polygon, previous.orientation, previous.bubble_id)
        np.testing.assert_array_equal(result.text_mask, previous.text_mask)
        np.testing.assert_array_equal(result.balloon_mask, previous.balloon_mask)
    children = [r for r in output if r.metadata.get('covered_parent_deduplication', {}).get('parent_bbox') == parent.bbox]
    assert len(children) == 2
    evidence = _union_masks([_window(p) for p in raw if p.label == 'text'
                            and p.bbox in [parent.bbox, *[r.metadata['raw_bbox'] for r in children]]])
    assert int(evidence.pixels.sum()) == 6711
    assert covered_components(evidence, [r.polygon for r in children])[evidence.pixels > 0].all()
    for a, b in zip(raw, before, strict=True):
        assert a.bbox == b.bbox
        np.testing.assert_array_equal(a.mask, b.mask)


def test_real_edge_variation_is_deterministic_under_query_permutation():
    w, h, raw = recorded('nested_edge_variation_predictions.json')
    def result(items):
        return [(r.bbox, r.polygon, r.bubble_id, r.metadata) for r in
                build_layout_regions(items, w, h, {}, page_key='permutation')]
    expected = result(raw)
    for seed in range(8):
        assert result([raw[i] for i in np.random.default_rng(seed).permutation(len(raw))]) == expected


def test_incomplete_title_children_are_not_mistaken_for_redundant_parent():
    w, h, raw = recorded('ambiguous_title_predictions.json')
    output = build_layout_regions(raw, w, h, {}, page_key='ambiguous-title')
    assert sum(not r.metadata['text_classification']['excluded'] for r in output) == 21
    # Four title boxes stay because deleting their parent would drop actual text.
    title = [r for r in output if 60 < r.bbox[0] < 520 and 340 < r.bbox[1] < 520
             and not r.metadata['text_classification']['excluded']]
    assert len(title) == 4


@pytest.mark.parametrize('defect', ['missing_character', 'shifted', 'punctuation', 'split_glyph'])
def test_component_fallback_does_not_accept_incomplete_or_misaligned_segmentation(defect):
    leaves = [leaf(20), leaf(60)]
    parent = container(leaves)
    # Imperfect parent edges deliberately force the component fallback.
    parent.mask[10:18, 8:10] = 0
    if defect == 'missing_character':
        parent.mask[10:18, 8:18] = 0
    elif defect == 'shifted':
        leaves[0].mask = np.roll(leaves[0].mask, 8, axis=1)
    elif defect == 'punctuation':
        parent.mask[50:52, 30:32] = 1
    else:
        parent.mask[35:38, 8:58] = 1
    items = [parent, *leaves]
    geo = geometry(items)
    assert covered_text_parents(items, dict.fromkeys(geo), geo, dict.fromkeys(geo), reserved=set(),
                                bubble_coverage=dict.fromkeys(geo, 1.)) == {}


@pytest.mark.parametrize('hole', [False, True])
def test_nested_original_evidence_is_not_forgotten_after_inner_parent_is_removed(hole):
    leaves = [leaf(20), leaf(60), leaf(100)]
    inner = container(leaves[:2])
    if hole:
        # Rescued by the first leaf's margin, NOT represented in its own mask.
        # This evidence must survive both inner and outer container removal.
        inner.mask[50:52, 3:5] = 1
    outer = container(leaves, 8)
    items = [outer, inner, *leaves]
    geo = geometry(items)
    result = covered_text_parents(items, dict.fromkeys(geo), geo, dict.fromkeys(geo),
                                   reserved=set(), bubble_coverage=dict.fromkeys(geo, 1.))
    assert result == {1: [2, 3], 0: [2, 3, 4]}
    ink = _union_masks([_window(p) for p in items])
    assert covered_components(ink, [geo[i][0] for i in (2, 3, 4)])[ink.pixels > 0].all()


def test_component_support_checks_small_marks_not_only_aggregate_overlap():
    pixels = np.zeros((10, 30), np.uint8)
    pixels[1:9, 1:20] = 1
    pixels[3, 27] = 1
    reference = pixels.copy()
    reference[3, 27] = 0
    assert not components_supported(_Mask(pixels, 8, 15), _dilate(_Mask(reference, 8, 15)))
    assert components_supported(_Mask(pixels, 8, 15), _dilate(_Mask(pixels, 8, 15)))
    assert not components_supported(_Mask(pixels, 8, 15), _Mask(pixels, 80, 150))
    assert not components_supported(_Mask(np.zeros_like(pixels), 8, 15), _Mask(pixels, 8, 15))


def test_equal_box_confidence_with_different_masks_has_stable_surviving_evidence():
    first = leaf(20)
    second = deepcopy(first)
    second.mask[5, 5] = 1
    def result(items):
        return [(p.bbox, p.mask_origin, p.mask.tobytes()) for p in resolve(items)]
    assert len(result([first, second])) == 1
    assert result([first, second]) == result([second, first])


@pytest.mark.parametrize('seed', range(32))
def test_seeded_jitter_and_duplicate_piles_preserve_ink_order_and_input_masks(seed):
    rng = np.random.default_rng(seed)
    leaves = [leaf(20+40*i, thin=bool(seed % 2)) for i in range(3)]
    items = [container(leaves, 8), container(leaves[:2]), *leaves]
    for source in list(items):
        for _ in range(2):
            copy = deepcopy(source)
            # Jitter detection bounds independently of the original lettering.
            dx, dy = (int(v) for v in rng.integers(-2, 3, size=2))
            copy.bbox[0] += dx
            copy.bbox[1] += dy
            copy.bbox[2] -= dx
            copy.bbox[3] -= dy
            copy.confidence = float(rng.uniform(.26, .99))
            if rng.random() < .5:
                copy.mask[10, 8] = 0  # A partial edge, not a disappearing glyph.
            items.append(copy)
    before = deepcopy(items)
    def result(values):
        return sorted((tuple(p.bbox), p.mask_origin, p.mask.tobytes()) for p in resolve(values, dialogue=bool(seed % 2)))
    expected = result(items)
    assert result(list(reversed(items))) == expected
    assert result([items[i] for i in rng.permutation(len(items))]) == expected
    for a, b in zip(items, before, strict=True):
        assert a.bbox == b.bbox
        np.testing.assert_array_equal(a.mask, b.mask)

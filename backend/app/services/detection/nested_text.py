"""Conservative de-duplication of whole-dialogue and per-column detections.

Only an existing enclosing prediction may replace its disjoint children. This
is not bubble-wide grouping: geometry, typography and bidirectional mask
coverage must agree. Ambiguous/missing segmentation leaves detections alone.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from app.services.detection.text_roles import LayoutPrediction

# Segmentation edges can differ by a pixel between RF-DETR queries. Require
# strong raw agreement as well, so dilation cannot bridge unrelated lettering.
MIN_RAW_COVERAGE = .90
MIN_EDGE_COVERAGE = .98
MIN_BOX_CONTAINMENT = .98


@dataclass
class _Mask:
    pixels: np.ndarray
    x: int
    y: int


def _window(item: LayoutPrediction) -> _Mask:
    x, y, w, h = item.bbox
    mx, my = item.mask_origin
    mh, mw = item.mask.shape
    left, top = max(mx, math.floor(x)), max(my, math.floor(y))
    right, bottom = min(mx+mw, math.ceil(x+w)), min(my+mh, math.ceil(y+h))
    if right <= left or bottom <= top:
        return _Mask(np.empty((0, 0), np.uint8), left, top)
    return _Mask((item.mask[top-my:bottom-my, left-mx:right-mx] > 0).astype(np.uint8), left, top)


def _dilate(mask: _Mask) -> _Mask:
    pixels = cv2.dilate(np.pad(mask.pixels, 1), np.ones((3, 3), np.uint8))
    return _Mask(pixels, mask.x-1, mask.y-1)


def _slices(inner: _Mask, outer: _Mask):
    ih, iw = inner.pixels.shape
    oh, ow = outer.pixels.shape
    left, top = max(inner.x, outer.x), max(inner.y, outer.y)
    right, bottom = min(inner.x+iw, outer.x+ow), min(inner.y+ih, outer.y+oh)
    if right <= left or bottom <= top:
        return None
    return ((slice(top-inner.y, bottom-inner.y), slice(left-inner.x, right-inner.x)),
            (slice(top-outer.y, bottom-outer.y), slice(left-outer.x, right-outer.x)))


def _coverage(inner: _Mask, outers: list[_Mask]) -> float:
    count = np.count_nonzero(inner.pixels)
    if not count:
        return 0.
    # Allocate only the detection's cropped mask, never a page-sized canvas.
    covered = np.zeros_like(inner.pixels)
    for outer in outers:
        slices = _slices(inner, outer)
        if slices is not None:
            own, other = slices
            covered[own] |= outer.pixels[other]
    return float(np.count_nonzero(inner.pixels & covered)/count)


def _box_coverage(inner: LayoutPrediction, outer: LayoutPrediction) -> float:
    x, y, w, h = inner.bbox
    a, b, c, d = outer.bbox
    intersection = max(0., min(x+w, a+c)-max(x, a))*max(0., min(y+h, b+d)-max(y, b))
    return intersection/max(1., w*h)


def _adjacent_lines(items: list[LayoutPrediction], direction: str, angle: float) -> bool:
    """Don't turn separated paragraphs/asides into a single dialogue block."""
    radians = math.radians(angle)
    cos, sin = math.cos(radians), math.sin(radians)
    lines = []
    for item in items:
        x, y, w, h = item.bbox
        points = np.asarray([(x, y), (x+w, y), (x+w, y+h), (x, y+h)])
        local = points @ np.asarray([[cos, -sin], [sin, cos]])
        along, across = (1, 0) if direction == "vertical" else (0, 1)
        lines.append((float(local[:, across].min()), float(local[:, across].max()),
                      float(local[:, along].min()), float(local[:, along].max())))
    lines.sort()
    for first, second in zip(lines, lines[1:], strict=False):
        overlap = max(0., min(first[3], second[3])-max(first[2], second[2]))
        if overlap < .6*min(first[3]-first[2], second[3]-second[2]):
            return False
        if second[0]-first[1] > 1.5*max(first[1]-first[0], second[1]-second[0]):
            return False
    return True


def nested_text_duplicates(
    predictions: list[LayoutPrediction],
    bubble_indices: dict[int, int | None],
    typography: dict[int, tuple[str, float]],
) -> dict[int, list[int]]:
    """Return parent -> redundant children; no input or masks are mutated.

    Require two or more separate children that jointly explain the parent.
    A single contained box, intersecting chains, free text and different
    bubbles are deliberately left to existing NMS/manual review.
    """
    texts = [i for i in typography if bubble_indices.get(i) is not None]
    def area(i: int) -> float:
        return predictions[i].bbox[2]*predictions[i].bbox[3]

    order = sorted(texts, key=lambda i: (area(i), *predictions[i].bbox, -predictions[i].confidence))
    masks: dict[int, _Mask] = {}
    expanded: dict[int, _Mask] = {}
    groups: dict[int, list[int]] = {}
    consumed: set[int] = set()

    def prepare(index: int) -> bool:
        if index not in masks:
            masks[index] = _window(predictions[index])
        if not np.count_nonzero(masks[index].pixels):
            return False
        if index not in expanded:
            expanded[index] = _dilate(masks[index])
        return True

    for parent in order:
        if parent in consumed:
            continue
        direction, angle = typography[parent]
        children = [i for i in order if i != parent and i not in consumed
                    and bubble_indices[i] == bubble_indices[parent]
                    and area(i) < area(parent)*.85
                    and _box_coverage(predictions[i], predictions[parent]) >= MIN_BOX_CONTAINMENT
                    and typography[i][0] == direction and abs(typography[i][1]-angle) <= 10.]
        if len(children) < 2:
            continue
        if not _adjacent_lines([predictions[i] for i in children], direction, angle):
            continue
        # Refuse nested chains/overlapping alternatives rather than greedily
        # merging an entire connected component across distinct paragraphs.
        if any(max(_box_coverage(predictions[a], predictions[b]),
                   _box_coverage(predictions[b], predictions[a])) > .2
               for offset, a in enumerate(children) for b in children[offset+1:]):
            continue
        if not all(prepare(i) for i in [parent, *children]):
            continue
        if any(_coverage(masks[i], [masks[parent]]) < MIN_RAW_COVERAGE
               or _coverage(masks[i], [expanded[parent]]) < MIN_EDGE_COVERAGE for i in children):
            continue  # A child has lettering not explained by the parent.
        if (_coverage(masks[parent], [masks[i] for i in children]) < MIN_RAW_COVERAGE
                or _coverage(masks[parent], [expanded[i] for i in children]) < MIN_EDGE_COVERAGE):
            continue  # The enclosing box contains additional, independent text.
        groups[parent] = children
        consumed.update([parent, *children])  # Never transitively combine groups.
    return groups

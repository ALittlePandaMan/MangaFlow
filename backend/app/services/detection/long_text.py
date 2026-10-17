"""Resolve nested, upright prose predictions without changing dialogue rules.

An enclosing mask is evidence for complete text lines, not a reason to merge
all text inside it. Partition only a verified single-column, unassigned block;
otherwise leave every prediction untouched. No OCR calls or image heuristics.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from app.services.detection.nested_text import _box_coverage, _coverage, _dilate, _Mask, _window
from app.services.detection.text_roles import LayoutPrediction


@dataclass
class TextPiece:
    bbox: list[float]
    owner: int
    lines: list[list[float]]


@dataclass
class TextPartition:
    children: list[int]
    pieces: list[TextPiece]


def _runs(active: np.ndarray) -> list[tuple[int, int]]:
    edges = np.flatnonzero(np.diff(np.r_[False, active, False]))
    return [(int(a), int(b)) for a, b in edges.reshape(-1, 2)]


def _lines(mask: _Mask) -> list[_Mask]:
    counts = mask.pixels.sum(axis=1)
    bands = _runs(counts >= max(3, mask.pixels.shape[1] * .02))
    if not 6 <= len(bands) <= 80:
        return []
    heights = np.asarray([b-a for a, b in bands])
    typical = float(np.median(heights))
    if typical < 3 or heights.min() < typical*.5 or heights.max() > typical*1.6:
        return []
    gaps = np.asarray([b[0]-a[1] for a, b in zip(bands, bands[1:], strict=False)])
    if gaps.min() < max(2, typical*.25) or np.median(gaps) > typical*2:
        return []
    # Retain faint punctuation/edge pixels adjoining each band. Any
    # unexplained island in the gaps invalidates the entire partition below.
    lines = []
    for a, b in bands:
        while a > 0 and counts[a-1]:
            a -= 1
        while b < len(counts) and counts[b]:
            b += 1
        lines.append(_Mask(mask.pixels[a:b], mask.x, mask.y+a))
    if any(first.y+first.pixels.shape[0] >= second.y for first, second in zip(lines, lines[1:], strict=False)):
        return []  # Low-density bridges must not join two supposed text lines.
    if _coverage(mask, lines) < 1.:
        return []
    # A row must look like a text line, not a vertical column or filled panel.
    if sum(np.count_nonzero(line.pixels.sum(axis=0)) >= typical*6 for line in lines) < 6:
        return []
    # A persistent wide gutter means separate columns, even if the wider
    # column alone accounts for most of each row's foreground pixels.
    for a, b in _runs(mask.pixels.sum(axis=0) == 0):
        if b-a > typical*2 and sum(bool(line.pixels[:, :a].any() and line.pixels[:, b:].any()) for line in lines) >= 3:
            return []
    if not .03 < float(mask.pixels.mean()) < .65:
        return []
    return lines


def _bounds(lines: list[_Mask]) -> list[float]:
    points = [(line, np.nonzero(line.pixels)) for line in lines]
    left = min(line.x+int(cols.min()) for line, (_, cols) in points)
    right = max(line.x+int(cols.max())+1 for line, (_, cols) in points)
    top = min(line.y+int(rows.min()) for line, (rows, _) in points)
    bottom = max(line.y+int(rows.max())+1 for line, (rows, _) in points)
    return list(map(float, (left, top, right-left, bottom-top)))


def long_text_partitions(
    predictions: list[LayoutPrediction],
    bubble_indices: dict[int, int | None],
    typography: dict[int, tuple[str, float]],
    panels: dict[int, int | None],
) -> dict[int, TextPartition]:
    eligible = {i for i, (direction, angle) in typography.items()
                if bubble_indices.get(i) is None and direction == "horizontal" and abs(angle) <= 2}
    def area(i: int) -> float:
        return predictions[i].bbox[2]*predictions[i].bbox[3]

    order = sorted(eligible, key=lambda i: (-area(i), *predictions[i].bbox, -predictions[i].confidence))
    consumed: set[int] = set()
    output = {}
    masks: dict[int, _Mask] = {}

    def mask(i: int) -> _Mask:
        if i not in masks:
            masks[i] = _window(predictions[i])
        return masks[i]

    for parent in order:
        if parent in consumed:
            continue
        enclosing = predictions[parent]
        # An unassigned parent can straddle several real bubbles. Never use
        # prose partitioning to override those associations or panel boundaries.
        if any(item.label in {"bubble", "panel"} and
               max(_box_coverage(item, enclosing), _box_coverage(enclosing, item)) > .05
               for i, item in enumerate(predictions) if i != panels.get(parent)):
            continue
        children = [i for i in typography if i != parent and area(i) < area(parent)*.85
                    and _box_coverage(predictions[i], enclosing) >= .9]
        if len(children) < 2 or any(i not in eligible or i in consumed or panels.get(i) != panels.get(parent) for i in children):
            continue
        if any(item.label in {"text", "onomatopoeia"} and i != parent and i not in children
               and max(_box_coverage(item, enclosing), _box_coverage(enclosing, item)) > .1
               for i, item in enumerate(predictions)):
            continue  # Do not resolve just one part of an intersecting chain.
        root = mask(parent)
        if not root.pixels.size:
            continue
        expanded = _dilate(root)
        if any(not mask(i).pixels.size or _coverage(mask(i), [root]) < .8
               or _coverage(mask(i), [expanded]) < .98 for i in children):
            continue  # Unique child lettering must never be thrown away.
        lines = _lines(root)
        if not lines:
            continue
        typical = float(np.median([line.pixels.shape[0] for line in lines]))
        normal_gap = float(np.median([b.y-a.y-a.pixels.shape[0] for a, b in zip(lines, lines[1:], strict=False)]))
        expanded_children = {i: _dilate(mask(i)) for i in children}
        owners = []
        ambiguous = False
        for line in lines:
            matches = [i for i in children if _coverage(line, [expanded_children[i]]) >= .6]
            if any(max(_box_coverage(predictions[a], predictions[b]), _box_coverage(predictions[b], predictions[a])) < .9
                   for offset, a in enumerate(matches) for b in matches[offset+1:]):
                ambiguous = True
                break
            owners.append(min(matches, key=lambda i: (area(i), -predictions[i].confidence, *predictions[i].bbox))
                          if matches else parent)
        if ambiguous:
            continue
        # At least two independently detected pieces must support the split;
        # near-duplicate full blocks or side-by-side columns aren't enough.
        if len(set(owners)-{parent}) < 2:
            continue
        pieces: list[TextPiece] = []
        start = 0
        for end in range(1, len(lines)+1):
            boundary = end == len(lines) or owners[end] != owners[start]
            if not boundary:
                previous = lines[end-1]
                boundary = lines[end].y-(previous.y+previous.pixels.shape[0]) > max(typical*.9, normal_gap*1.5)
            if boundary:
                segment = lines[start:end]
                pieces.append(TextPiece(_bounds(segment), owners[start], [_bounds([line]) for line in segment]))
                start = end
        # Every child needs a complete explanation in the final frames, even
        # if it lost ownership to a smaller nested candidate. Check both mask
        # support above and geometric coverage here, including punctuation.
        frames = [_Mask(np.ones((int(p.bbox[3])+2, int(p.bbox[2])+2), np.uint8), int(p.bbox[0])-1, int(p.bbox[1])-1)
                  for p in pieces]
        if any(_coverage(mask(i), frames) < 1. for i in [parent, *children]):
            continue
        output[parent] = TextPartition(children, pieces)
        consumed.update([parent, *children])
    return output

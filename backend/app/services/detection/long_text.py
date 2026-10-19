"""Recover complete lines from nested, upright prose predictions.

An enclosing mask is evidence for complete text lines, not a reason to merge
all text inside it. Partition only a verified single-column, unassigned block;
otherwise leave every prediction untouched for the shared redundancy resolver.
No OCR calls or image heuristics.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from app.services.detection.mask_evidence import _box_coverage, _coverage, _dilate, _Mask, _union_masks, _window
from app.services.detection.mask_geometry import covered_components
from app.services.detection.text_roles import LayoutPrediction
from app.utils.geometry import bbox_to_polygon


@dataclass
class TextPiece:
    bbox: list[float]
    owner: int
    lines: list[list[float]]
    lettering: _Mask


@dataclass
class TextPartition:
    children: list[int]
    pieces: list[TextPiece]
    orientation: str = "horizontal"
    method: str = "verified_horizontal_lines"


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


def _complete_lines(root: _Mask, children: list[_Mask], lines: list[_Mask]) -> list[_Mask]:
    """Reconcile imperfect instance masks without inventing or dropping ink.

    A large query sometimes misses strokes that its smaller paragraph queries
    see. Union their ORIGINAL foreground, then require exactly the same verified
    line structure. Extra lines, punctuation islands and joined rows abort the
    split. This is transient detection evidence, never a repair-mask dilation.
    """
    completed = _lines(_union_masks([root, *children]))
    if len(completed) != len(lines):
        return []
    for old, new in zip(lines, completed, strict=True):
        overlap = min(old.y+old.pixels.shape[0], new.y+new.pixels.shape[0])-max(old.y, new.y)
        if overlap < .8*min(old.pixels.shape[0], new.pixels.shape[0]):
            return []
    return completed


def _horizontal_partitions(
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
                    and _box_coverage(predictions[i], enclosing) >= .8]
        if not children or any(i not in eligible or i in consumed or panels.get(i) != panels.get(parent) for i in children):
            continue
        if any(item.label in {"text", "onomatopoeia"} and i != parent and i not in children
               and max(_box_coverage(item, enclosing), _box_coverage(enclosing, item)) > .1
               for i, item in enumerate(predictions)):
            continue  # Do not resolve just one part of an intersecting chain.
        root = mask(parent)
        if not root.pixels.size:
            continue
        expanded = _dilate(root)
        lines = _lines(root)
        if not lines:
            continue
        if any(not mask(i).pixels.size or not 0 < float(mask(i).pixels.mean()) < .8 for i in children):
            continue
        if any(_coverage(mask(i), [root]) < 1. for i in children):
            # Even one child-only edge pixel belongs to the evidence, including
            # candidates that lose paragraph ownership. Reconcile exact ink,
            # anchored to the verified parent lines; never round it away.
            if any(_coverage(mask(i), [expanded]) < .35 for i in children):
                continue
            lines = _complete_lines(root, [mask(i) for i in children], lines)
            if not lines:
                continue
        typical = float(np.median([line.pixels.shape[0] for line in lines]))
        normal_gap = float(np.median([b.y-a.y-a.pixels.shape[0] for a, b in zip(lines, lines[1:], strict=False)]))
        expanded_children = {i: _dilate(mask(i)) for i in children}
        # Prefer boundary sources by containment of VERIFIED lettering lines,
        # not rectangle similarity. All alternative masks still participate in
        # completion and the final all-foreground crop check. No evidence is
        # deleted when a candidate loses paragraph-boundary preference.
        supported_lines = {i: frozenset(k for k, line in enumerate(lines)
                                       if _coverage(line, [expanded_children[i]]) >= .6) for i in children}
        preferred = []
        for i in sorted(children, key=lambda i: (-predictions[i].confidence, *predictions[i].bbox)):
            if supported_lines[i] and not any(supported_lines[i] <= supported_lines[j]
                                               or supported_lines[j] <= supported_lines[i] for j in preferred):
                preferred.append(i)
        owners = []
        ambiguous = False
        for line_index in range(len(lines)):
            matches = [i for i in preferred if line_index in supported_lines[i]]
            if any(max(_box_coverage(predictions[a], predictions[b]), _box_coverage(predictions[b], predictions[a])) < .9
                   for offset, a in enumerate(matches) for b in matches[offset+1:]):
                ambiguous = True
                break
            owners.append(min(matches, key=lambda i: (area(i), -predictions[i].confidence, *predictions[i].bbox))
                          if matches else parent)
        if ambiguous:
            continue
        # Keep the existing multi-child evidence requirement; a single child
        # may support a split too. Nested alternatives can lose ownership, but
        # every foreground pixel still needs a complete final crop below.
        if len(set(owners)-{parent}) < min(2, len(children)):
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
                pieces.append(TextPiece(_bounds(segment), owners[start], [_bounds([line]) for line in segment],
                                        _union_masks(segment)))
                start = end
        # Every child needs a complete explanation in the final frames, even
        # if it lost ownership to a smaller nested candidate. Check both mask
        # support above and geometric coverage here, including punctuation.
        frames = [bbox_to_polygon(p.bbox) for p in pieces]
        ink = _union_masks([piece.lettering for piece in pieces])
        if not covered_components(ink, frames)[ink.pixels > 0].all():
            continue
        output[parent] = TextPartition(children, pieces)
        consumed.update([parent, *children])
    return output


def _gutter_partitions(predictions, bubble_indices, typography, panels, reserved):
    """Split dense containers at true whitespace, retaining ALL original ink.

    Large RF-DETR queries can have fused strokes and nested alternatives, so
    requiring clean individual lines rejects otherwise clear paragraphs. A
    wide EMPTY projection gap proves separate complete crops without deciding
    which query's contour is correct. Same-context children establish writing
    direction, even when the aggregate container's aspect suggests the other.
    """
    def area(i):
        return predictions[i].bbox[2]*predictions[i].bbox[3]

    output, consumed = {}, set(reserved)
    masks = {i: _window(predictions[i]) for i in typography}
    for parent in sorted(typography, key=lambda i: (-area(i), *predictions[i].bbox)):
        if parent in consumed or abs(typography[parent][1]) > 2:
            continue
        children = [i for i in typography if i != parent and area(i) < area(parent)*.85
                    and _box_coverage(predictions[i], predictions[parent]) >= .8]
        if not children or any(i in consumed or panels.get(i) != panels.get(parent)
                               or bubble_indices.get(i) != bubble_indices.get(parent) for i in children):
            continue
        direction = typography[children[0]][0]
        if any(typography[i][0] != direction or abs(typography[i][1]) > 2 for i in children):
            continue
        # A parent cannot borrow evidence across a bubble/panel, nor erase an
        # overlapping independent phrase/SFX. Compatible ancestors are left
        # for their own proof, not silently treated as redundant.
        own_context = {panels.get(parent), bubble_indices.get(parent)}
        ancestors = {i for i in typography if area(i) > area(parent)
                     and panels.get(i) == panels.get(parent) and bubble_indices.get(i) == bubble_indices.get(parent)
                     and _box_coverage(predictions[parent], predictions[i]) >= .8}
        if any(i not in {parent, *children, *ancestors} and i not in consumed
               and (item.label in {"bubble", "panel"} and i not in own_context
                    or item.label in {"text", "onomatopoeia"})
               and max(_box_coverage(item, predictions[parent]), _box_coverage(predictions[parent], item)) > .05
               for i, item in enumerate(predictions)):
            continue
        evidence = [masks[i] for i in [parent, *children]]
        if any(not m.pixels.size or not .03 < float(m.pixels.mean()) < .65 for m in evidence):
            continue
        bubble = bubble_indices.get(parent)
        if bubble is not None:
            if not 0 <= bubble < len(predictions) or predictions[bubble].label != "bubble":
                continue
            balloon = _window(predictions[bubble])
            if any(_coverage(m, [balloon]) < .95 for m in evidence):
                continue
        support = _dilate(masks[parent])
        if any(_coverage(masks[i], [support]) < .35 for i in children):
            continue
        original = _union_masks(evidence)
        ink = original if direction == "horizontal" else _Mask(original.pixels.T, original.y, original.x)
        counts = ink.pixels.sum(axis=1)
        bands = _runs(counts >= max(3, ink.pixels.shape[1]*.02))
        if not 6 <= len(bands) <= 80:
            continue
        typical = float(np.median([b-a for a,b in bands]))
        normal = [(a,b) for a,b in bands if typical*.5 <= b-a <= typical*1.8]
        if typical < 3 or len(normal) < max(6, len(bands)*.65):
            continue
        if sum(np.count_nonzero(ink.pixels[a:b].sum(axis=0)) >= typical*6 for a,b in normal) < 6:
            continue
        # A persistent perpendicular gutter indicates independent columns,
        # not one paragraph whose lines can be recombined across the gutter.
        if any(b-a > typical*2 and sum(bool(ink.pixels[y:z, :a].any() and ink.pixels[y:z, b:].any())
                                       for y,z in normal) >= 3
               for a,b in _runs(ink.pixels.sum(axis=0) == 0)):
            continue
        # Only exact empty stripes can separate crops; a faint punctuation
        # mark or a one-pixel bridge is never discarded as "almost whitespace".
        empty_runs = [(a,b) for a,b in _runs(counts == 0) if a > 0 and b < len(counts)]
        normal_gap = float(np.median([b-a for a,b in empty_runs])) if empty_runs else 0.
        gaps = [(a,b) for a,b in empty_runs if b-a >= max(4, typical*.9, normal_gap*1.8)]
        if not gaps:
            continue
        boundaries = [0, *[(a+b)//2 for a,b in gaps], len(counts)]
        pieces = []
        for start, end in zip(boundaries, boundaries[1:], strict=False):
            part = _Mask(ink.pixels[start:end], ink.x, ink.y+start)
            if np.count_nonzero(part.pixels) < typical**2*4:
                pieces = []
                break  # Do not strand a title, small mark or noise as a paragraph.
            if direction == "vertical":
                part = _Mask(part.pixels.T, part.y, part.x)
            box = _bounds([part])
            owner = max([parent, *children], key=lambda i: (_coverage(part, [masks[i]]), -area(i)))
            pieces.append(TextPiece(box, owner, _ocr_lines(part, direction, typical), part))
        if len(pieces) < 2:
            continue
        frames = [bbox_to_polygon(piece.bbox) for piece in pieces]
        if not covered_components(original, frames)[original.pixels > 0].all():
            continue
        # At least one resulting paragraph must be independently evidenced by
        # a child query. Unique parent-only paragraphs are kept, not deleted.
        if not any(_coverage(piece.lettering, [masks[i]]) >= .6 for piece in pieces for i in children):
            continue
        if direction == "vertical":
            pieces.reverse()  # Japanese columns/paragraphs read right-to-left.
        output[parent] = TextPartition(children, pieces, direction, "verified_prose_gutters")
        consumed.update([parent, *children])
    return output


def _ocr_lines(mask, direction, typical):
    """Bound local OCR crops without ever cutting an original ink component.

    Tiny detached accents join their nearest line/column, not a separate OCR
    job. Fused query contours stay together; only completely empty stripes
    permit a split. Region grouping and the persisted repair mask stay intact.
    """
    ink = mask if direction == "horizontal" else _Mask(mask.pixels.T, mask.y, mask.x)
    bands = _runs(ink.pixels.sum(axis=1) > 0)
    while len(bands) > 1:
        tiny = next((i for i,(a,b) in enumerate(bands) if b-a < typical*.45), None)
        if tiny is None:
            break
        before = bands[tiny][0]-bands[tiny-1][1] if tiny else float("inf")
        after = bands[tiny+1][0]-bands[tiny][1] if tiny+1 < len(bands) else float("inf")
        left = tiny-1 if before <= after else tiny
        bands[left:left+2] = [(bands[left][0], bands[left+1][1])]
    boxes = []
    for a,b in bands:
        part = _Mask(ink.pixels[a:b], ink.x, ink.y+a)
        if direction == "vertical":
            part = _Mask(part.pixels.T, part.y, part.x)
        boxes.append(_bounds([part]))
    if direction == "vertical":
        boxes.reverse()
    if not boxes or not covered_components(mask, [bbox_to_polygon(box) for box in boxes])[mask.pixels > 0].all():
        return [_bounds([mask])]
    return boxes


def long_text_partitions(predictions, bubble_indices, typography, panels):
    output = _horizontal_partitions(predictions, bubble_indices, typography, panels)
    reserved = {i for parent, part in output.items() for i in [parent, *part.children]}
    output.update(_gutter_partitions(predictions, bubble_indices, typography, panels, reserved))
    return output

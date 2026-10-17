"""Remove redundant free-text parents only when surviving children explain them.

Dialogue aggregation and prose partitioning own their candidates first. This
pass never overrides either decision and never uses a soon-to-be-removed box
as coverage evidence. Missing or ambiguous segmentation leaves the boxes alone.
"""
from __future__ import annotations

import numpy as np
from app.services.detection.mask_geometry import covered_components
from app.services.detection.nested_text import _box_coverage, _coverage, _dilate, _Mask, _window
from app.services.detection.text_roles import LayoutPrediction


def _frames_cover(mask: _Mask, polygons: list[list[list[float]]]) -> bool:
    """Every foreground pixel centre must lie in a retained convex frame.

    Test the actual padded/rotated polygons, not their enclosing rectangle or
    the bounding rectangle of the whole group. Chunking bounds temporary memory.
    """
    frames = []
    for polygon in polygons:
        points = np.asarray(polygon, dtype=float)
        if points.shape != (4, 2) or not np.isfinite(points).all():
            return False
        edges = np.roll(points, -1, axis=0)-points
        area = float(np.sum(points[:, 0]*np.roll(points[:, 1], -1)-points[:, 1]*np.roll(points[:, 0], -1)))
        if abs(area) < 1e-6:
            return False
        frames.append((points, edges, 1 if area > 0 else -1))
    for start in range(0, mask.pixels.shape[0], 128):
        rows, cols = np.nonzero(mask.pixels[start:start+128])
        x, y = cols+mask.x+.5, rows+mask.y+start+.5
        covered = np.zeros(len(rows), dtype=bool)
        for points, edges, sign in frames:
            inside = np.ones(len(rows), dtype=bool)
            for (px, py), (dx, dy) in zip(points, edges, strict=True):
                inside &= sign*(dx*(y-py)-dy*(x-px)) >= -1e-6
            covered |= inside
        if not covered.all():
            return False
    return bool(np.count_nonzero(mask.pixels))


def covered_text_parents(
    predictions: list[LayoutPrediction],
    bubble_indices: dict[int, int | None],
    geometries: dict[int, tuple[list[list[float]], str, float]],
    panels: dict[int, int | None],
    *,
    reserved: set[int],
) -> dict[int, list[int]]:
    """Return removable parent -> retained children, without mutating inputs."""
    def area(i: int) -> float:
        return predictions[i].bbox[2]*predictions[i].bbox[3]

    def overlap(a: int, b: int) -> float:
        return max(_box_coverage(predictions[a], predictions[b]), _box_coverage(predictions[b], predictions[a]))

    def key(i: int) -> tuple:
        return -area(i), *predictions[i].bbox, -predictions[i].confidence

    eligible = {i for i in geometries if bubble_indices.get(i) is None and i not in reserved}
    occupied = set(reserved)
    masks: dict[int, _Mask] = {}
    expanded: dict[int, _Mask] = {}
    groups: dict[int, list[int]] = {}

    def prepare(i: int) -> bool:
        if i not in masks:
            masks[i] = _window(predictions[i])
        pixels = masks[i].pixels
        if not pixels.size or not 0 < float(pixels.mean()) < .8:
            return False  # Empty/filled segmentation isn't evidence of lettering.
        if i not in expanded:
            expanded[i] = _dilate(masks[i])
        return True

    for parent in sorted(eligible, key=key):
        if parent in occupied:
            continue
        # Even an unassigned text may straddle real balloons or panel borders.
        if any(item.label in {"bubble", "panel"} and overlap(parent, i) > .05
               for i, item in enumerate(predictions) if i != panels.get(parent)):
            continue
        children = sorted((i for i in geometries if i != parent and area(i) < area(parent)*.85
                           and _box_coverage(predictions[i], predictions[parent]) >= .98), key=key)
        if len(children) < 2 or any(i not in eligible or i in occupied or panels.get(i) != panels.get(parent) for i in children):
            continue
        # Only independent children qualify; nested alternatives and intersecting
        # chains require a different resolver. Never recursively remove evidence.
        if any(overlap(a, b) > .2 for offset, a in enumerate(children) for b in children[offset+1:]):
            continue
        if any(item.label in {"text", "onomatopoeia"} and overlap(parent, i) > .1
               for i, item in enumerate(predictions) if i != parent and i not in children):
            continue
        direction, angle = geometries[children[0]][1:]
        if any(geometries[i][1] != direction or abs(geometries[i][2]-angle) > 10 for i in children):
            continue
        # The aggregate parent's direction is deliberately NOT used: combining
        # two horizontal blocks can make its shape appear vertical.
        if not all(prepare(i) for i in [parent, *children]):
            continue
        if any(_coverage(masks[i], [masks[parent]]) < .9 or _coverage(masks[i], [expanded[parent]]) < .98 for i in children):
            continue
        if (_coverage(masks[parent], [masks[i] for i in children]) < .9
                or _coverage(masks[parent], [expanded[i] for i in children]) < .98):
            continue
        # A 99% aggregate score can still miss a whole punctuation mark. All
        # parent foreground must survive in the final child crops, no exceptions.
        frames = [geometries[i][0] for i in children]
        if not _frames_cover(masks[parent], frames):
            continue
        # A glyph split between two children passes union coverage, but neither
        # OCR crop contains the complete glyph. Keep its enclosing candidate.
        if not covered_components(masks[parent], frames)[masks[parent].pixels > 0].all():
            continue
        groups[parent] = children
        occupied.update([parent, *children])
    return groups

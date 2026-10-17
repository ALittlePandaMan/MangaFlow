"""Preserve uncovered lettering during redetection without editing user frames."""
from __future__ import annotations

import math
from dataclasses import replace

import cv2
import numpy as np
from app.services.base import DetectionResult
from app.services.detection.layout import text_box_padding
from app.services.detection.long_text import _runs
from app.services.detection.mask_geometry import covered_components, points_inside, polygon_points
from app.services.detection.nested_text import _Mask
from app.services.layout.text_rotation import can_initialize_rotation, free_text_angle
from app.utils.geometry import bbox_to_polygon


def preserved_free_text_angles(preserved: list, detected: list[DetectionResult]):
    """Match lettering one-to-one; never turn a container into a text angle.

    Use all candidates when checking ambiguity, including dialogue. A broad
    preserved frame containing multiple candidates must never inherit just
    one free-text child's angle. No frames, masks or texts are changed here.
    """
    if (not any(can_initialize_rotation(region) for region in preserved)
            or not any(free_text_angle(result.metadata, result.bubble_id) is not None for result in detected)):
        return
    matches: dict[int, list[int]] = {}
    owners: dict[int, list[int]] = {}
    for index, result in enumerate(detected):
        if result.text_mask is None or result.text_mask_origin is None:
            continue
        pixels = result.text_mask
        if pixels.ndim != 2:
            continue
        rows, cols = np.nonzero(pixels)
        if len(rows) < 24:
            continue
        stride = max(1, math.ceil(len(rows)/8192))
        x = cols[::stride]+result.text_mask_origin[0]+.5
        y = rows[::stride]+result.text_mask_origin[1]+.5
        for ri, region in enumerate(preserved):
            points = polygon_points(region.polygon)
            if points is None:
                continue
            left, top, width, height = region.bbox
            a, b, w, h = result.bbox
            overlap = max(0., min(left+width, a+w)-max(left, a))*max(0., min(top+height, b+h)-max(top, b))
            # Count a candidate's owners before the tighter compatibility
            # check: duplicate/small protected frames are ambiguity too.
            if overlap <= 0:
                continue
            coverage = float(points_inside(x, y, points).mean())
            if coverage < .1:
                continue
            matches.setdefault(ri, []).append(index)
            if coverage >= .95:
                owners.setdefault(index, []).append(ri)
    for ri, indices in matches.items():
        region = preserved[ri]
        if len(indices) != 1 or not can_initialize_rotation(region):
            continue
        index = indices[0]
        result = detected[index]
        if (owners.get(index) != [ri] or result.orientation != region.orientation
                or free_text_angle(result.metadata, result.bubble_id) is None):
            continue
        a, b, w, h = result.bbox
        x, y, width, height = region.bbox
        overlap = max(0., min(x+width, a+w)-max(x, a))*max(0., min(y+height, b+h)-max(y, b))
        union = width*height+w*h-overlap
        if union > 0 and overlap/union >= .65:
            yield region, result.metadata


def _polygons(preserved: list) -> list:
    return [getattr(region, "polygon", None) or bbox_to_polygon(region.bbox) for region in preserved]


def protected_duplicate_candidates(result: DetectionResult, preserved: list) -> list[DetectionResult]:
    """Restore columns only when every parent glyph survives in a final crop.

    Nested deduplication tolerates small segmentation differences. Those are
    harmless while retaining the parent, but may be unique punctuation when
    replacing it with its children during protected-frame redetection.
    """
    mask = _lettering_mask(result)
    if mask is None or not result.duplicate_members:
        return [result]
    frames = [*_polygons(preserved), *(member.polygon for member in result.duplicate_members)]
    covered = covered_components(mask, frames)
    return result.duplicate_members if covered[mask.pixels > 0].all() else [result]


def frame_is_preserved(polygon: list, preserved: list) -> bool:
    """No mask: require full geometric containment, not smaller-box overlap.

    Restrict this fallback to convex containers: all vertices being inside a
    concave polygon does not prove that the edges/interior are covered.
    """
    own = polygon_points(polygon)
    if own is None:
        return False
    for polygon in _polygons(preserved):
        points = polygon_points(polygon)
        if (points is not None and cv2.isContourConvex(points.astype(np.float32))
                and points_inside(own[:, 0], own[:, 1], points).all()):
            return True
    return False


def _lettering_mask(result: DetectionResult) -> _Mask | None:
    pixels, origin = result.text_mask, result.text_mask_origin
    if pixels is None or origin is None or pixels.ndim != 2:
        return None
    x, y, w, h = result.bbox
    mx, my = origin
    left, top = max(mx, math.floor(x)), max(my, math.floor(y))
    right, bottom = min(mx+pixels.shape[1], math.ceil(x+w)), min(my+pixels.shape[0], math.ceil(y+h))
    if right <= left or bottom <= top:
        return None
    pixels = (pixels[top-my:bottom-my, left-mx:right-mx] > 0).astype(np.uint8)
    if not 0 < float(pixels.mean()) < .8:
        return None
    return _Mask(pixels, left, top)


def exclude_preserved_text(result: DetectionResult, preserved: list) -> list[DetectionResult] | None:
    """None means no reliable mask; caller must use conservative geometry.

    Remove only whole, singly-covered ink components. For partial overlap,
    split upright text at empty projection bands only. Ambiguous/rotated text
    stays intact rather than losing letters or clipping strokes at user edges.
    """
    mask = _lettering_mask(result)
    if mask is None:
        return None
    pixels, left, top = mask.pixels, mask.x, mask.y
    x, y, w, h = result.bbox
    covered = covered_components(mask, _polygons(preserved))
    if covered[pixels > 0].all():
        return []
    if not covered.any():
        return [result]
    # Do not replace a rotated/perspective frame by an axis-aligned crop.
    points = polygon_points(result.polygon)
    if (abs(float(result.metadata.get("text_angle", 0.))) > 1e-6
            or points is None or points.shape != (4, 2)
            or not np.allclose(points, bbox_to_polygon(result.bbox), rtol=0, atol=1e-6)):
        return [result]
    vertical = result.orientation == "vertical"
    projection = pixels.any(axis=0 if vertical else 1)
    bands = _runs(projection)
    retained = []
    for a, b in bands:
        ink = pixels[:, a:b] if vertical else pixels[a:b]
        own = covered[:, a:b] if vertical else covered[a:b]
        retained.append(not own[ink > 0].all())
    if all(retained):
        return [result]  # No complete column/line can safely be removed.

    outputs = []
    for start, end in _runs(np.asarray(retained)):
        a, b = bands[start][0], bands[end-1][1]
        offset = left if vertical else top
        lo, hi = (x, x+w) if vertical else (y, y+h)
        padding = text_box_padding(result.metadata.get("text_box_padding", 3.))
        before = padding["left" if vertical else "top"]
        after = padding["right" if vertical else "bottom"]
        lo, hi = max(lo, offset+a-before), min(hi, offset+b+after)
        if start:
            lo = max(lo, offset+(bands[start-1][1]+a)/2)
        if end < len(bands):
            hi = min(hi, offset+(b+bands[end][0])/2)
        bbox = [lo, y, hi-lo, h] if vertical else [x, lo, w, hi-lo]
        metadata = {**result.metadata, "preserved_text_partition": {
            "method": "complete_mask_bands", "version": 1, "source_bbox": list(result.bbox),
        }}
        # These bounds apply only to this piece; stale grouping must not drive
        # mask generation or OCR with the old whole-block source polygons.
        metadata.pop("line_grouping", None)
        outputs.append(replace(result, bbox=bbox, polygon=bbox_to_polygon(bbox),
                               duplicate_members=[], metadata=metadata))
    return list(reversed(outputs)) if vertical else outputs

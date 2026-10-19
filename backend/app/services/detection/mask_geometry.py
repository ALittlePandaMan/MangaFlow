"""Pixel-centre geometry tests shared by conservative detection filters."""
from __future__ import annotations

import cv2
import numpy as np
from app.services.detection.mask_evidence import _Mask


def polygon_points(polygon) -> np.ndarray | None:
    try:
        points = np.asarray(polygon, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or not np.isfinite(points).all():
        return None
    if abs(cv2.contourArea(points.astype(np.float32))) < 1e-6:
        return None
    return points


def points_inside(x: np.ndarray, y: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Even/odd fill plus boundary, including concave user polygons."""
    inside = np.zeros(x.shape, dtype=bool)
    boundary = np.zeros(x.shape, dtype=bool)
    for (ax, ay), (bx, by) in zip(points, np.roll(points, -1, axis=0), strict=True):
        dx, dy = bx-ax, by-ay
        boundary |= ((abs(dx*(y-ay)-dy*(x-ax)) <= 1e-6)
                     & (x >= min(ax, bx)-1e-6) & (x <= max(ax, bx)+1e-6)
                     & (y >= min(ay, by)-1e-6) & (y <= max(ay, by)+1e-6))
        if abs(dy) > 1e-12:
            inside ^= ((ay > y) != (by > y)) & (x < ax+(y-ay)*dx/dy)
    return inside | boundary


def covered_components(mask: _Mask, polygons: list) -> np.ndarray:
    """Only mark a connected ink component covered if ONE frame contains it.

    Union coverage alone permits two crops to bisect the same glyph. Work on
    cropped masks and bounded row chunks, with no page-sized allocation.
    """
    count, labels = cv2.connectedComponents((mask.pixels > 0).astype(np.uint8), connectivity=8)
    covered = np.zeros(count, dtype=bool)
    for polygon in polygons:
        points = polygon_points(polygon)
        if points is None:
            continue
        fits = np.ones(count, dtype=bool)
        fits[0] = False
        for start in range(0, labels.shape[0], 128):
            chunk = labels[start:start+128]
            rows, cols = np.nonzero(chunk)
            inside = points_inside(cols+mask.x+.5, rows+mask.y+start+.5, points)
            fits[chunk[rows[~inside], cols[~inside]]] = False
        covered |= fits
        if covered[1:].all():
            break
    return covered[labels]


def components_supported(mask: _Mask, support: _Mask) -> bool:
    """Every ink component must have a majority match in a reference mask.

    A global overlap score hides wholly unsupported small characters. Count
    matches separately, including one-pixel punctuation. Callers may supply a
    transient one-pixel edge tolerance; neither input is changed.
    """
    if not mask.pixels.size or not np.any(mask.pixels):
        return False
    count, labels = cv2.connectedComponents((mask.pixels > 0).astype(np.uint8), connectivity=8)
    sizes = np.bincount(labels.ravel(), minlength=count)
    left, top = max(mask.x, support.x), max(mask.y, support.y)
    right = min(mask.x+mask.pixels.shape[1], support.x+support.pixels.shape[1])
    bottom = min(mask.y+mask.pixels.shape[0], support.y+support.pixels.shape[0])
    if right <= left or bottom <= top:
        return False
    own = labels[top-mask.y:bottom-mask.y, left-mask.x:right-mask.x]
    reference = support.pixels[top-support.y:bottom-support.y, left-support.x:right-support.x] > 0
    matches = np.bincount(own[reference], minlength=count)
    return bool(np.all(2*matches[1:] >= sizes[1:]))

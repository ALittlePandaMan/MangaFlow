"""Pixel-centre geometry tests shared by conservative detection filters."""
from __future__ import annotations

import cv2
import numpy as np
from app.services.detection.nested_text import _Mask


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

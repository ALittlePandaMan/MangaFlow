"""Bounded, immutable mask evidence shared by text detection postprocessing."""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from app.services.detection.text_roles import LayoutPrediction


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
    """Transient 1px query-edge tolerance, never a persisted repair mask."""
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


def _box_iou(first: LayoutPrediction, second: LayoutPrediction) -> float:
    x, y, w, h = first.bbox
    a, b, c, d = second.bbox
    intersection = max(0., min(x+w, a+c)-max(x, a))*max(0., min(y+h, b+d)-max(y, b))
    return intersection / max(1., w*h+c*d-intersection)


def _union_masks(masks: list[_Mask]) -> _Mask:
    """Combine ORIGINAL evidence in a bounded crop, without growing any stroke."""
    left, top = min(m.x for m in masks), min(m.y for m in masks)
    right = max(m.x+m.pixels.shape[1] for m in masks)
    bottom = max(m.y+m.pixels.shape[0] for m in masks)
    pixels = np.zeros((bottom-top, right-left), np.uint8)
    for mask in masks:
        h, w = mask.pixels.shape
        pixels[mask.y-top:mask.y-top+h, mask.x-left:mask.x-left+w] |= mask.pixels
    return _Mask(pixels, left, top)

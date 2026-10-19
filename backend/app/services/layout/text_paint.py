"""Bounded black/white lettering paint inferred from source ink and background.

Koharu reference: detection.rs::infer_typography/infer_text_paint at
4df6a4f785e8c2056e25e112eb7b89ccf2f040cc. Keep foreground samples separate
from the surrounding background; adapt only black/white here, not RGB style.
Nothing in this module changes the detector or repair mask.
"""
from __future__ import annotations

import math

import numpy as np
from PIL import Image, ImageDraw

PAINT_FIELDS = {"text_color", "stroke_color", "stroke_width"}
BLACK, WHITE = "#000000", "#ffffff"


def uses_auto_paint(region) -> bool:
    if getattr(region, "locked", False):
        return False
    layout = getattr(region, "layout_data", None) or {}
    mode = (layout.get("text_paint") or {}).get("mode")
    if mode is not None:
        return mode == "auto"
    # Old detected default styles can adopt the feature on their next render.
    # Preserve legacy custom colors/outlines and user-created/adjusted frames.
    return (bool(layout.get("detection")) and not layout.get("manual")
            and str(region.text_color).lower() in {"#111111", BLACK}
            and str(region.stroke_color).lower() == WHITE and region.stroke_width == 0)


def _linear_luminance(rgb):
    values = np.asarray(rgb, dtype=np.float32) / 255
    values = np.where(values <= .04045, values/12.92, ((values+.055)/1.055)**2.4)
    return values @ np.asarray([.2126, .7152, .0722], dtype=np.float32)


def source_paint(image, bbox, mask=None, origin=None):
    """Small source-ink hint, retaining neither image pixels nor a new mask."""
    x, y, w, h = bbox
    left, top = max(0, math.floor(x)-3), max(0, math.floor(y)-3)
    right, bottom = min(image.width, math.ceil(x+w)+3), min(image.height, math.ceil(y+h)+3)
    hint = {"version": 1, "mode": "auto", "method": "source_mask_and_background"}
    if right <= left or bottom <= top:
        return hint
    size = (min(right-left, 256), min(bottom-top, 256))
    crop = image.resize(size, Image.Resampling.BILINEAR, box=(left, top, right, bottom)).convert("RGB")
    ink = np.zeros((size[1], size[0]), dtype=bool)
    if mask is not None and origin is not None and mask.ndim == 2:
        mx, my = origin
        ys = np.floor((np.arange(size[1])+.5)*(bottom-top)/size[1]).astype(int)+top-my
        xs = np.floor((np.arange(size[0])+.5)*(right-left)/size[0]).astype(int)+left-mx
        if mask.size:
            valid = ((ys >= 0) & (ys < mask.shape[0]))[:, None] & ((xs >= 0) & (xs < mask.shape[1]))[None, :]
            ink = (mask[ys.clip(0, mask.shape[0]-1)[:, None], xs.clip(0, mask.shape[1]-1)[None, :]] > 0) & valid
    luminance = _linear_luminance(np.asarray(crop))
    border = np.zeros(ink.shape, dtype=bool)
    border[:2] = border[-2:] = True
    border[:, :2] = border[:, -2:] = True
    background = luminance[border & ~ink]
    if not background.size:
        background = luminance[~ink] if (~ink).any() else luminance.ravel()
    base = float(np.median(background))
    hint["source_background_luminance"] = round(base, 4)
    if ink.sum() >= 8 and ink.mean() < .8:
        low, high = np.quantile(luminance[ink], [.15, .85])
        candidate = low if abs(low-base) > abs(high-base) else high
        if abs(candidate-base) >= .12:
            hint["source_fill"] = BLACK if candidate < base else WHITE
    return hint


def background_luminance(image, region, bbox):
    x, y, w, h = bbox
    polygon = getattr(region, "translated_polygon", None) or getattr(region, "polygon", None)
    if polygon and len(polygon) < 3:
        polygon = None
    if polygon and len(polygon) >= 3:
        points = np.asarray(polygon, dtype=float)
        if points.ndim == 2 and points.shape[1] == 2 and np.isfinite(points).all():
            x, y = points.min(axis=0)
            right, bottom = points.max(axis=0)
            w, h = right-x, bottom-y
        else:
            polygon = None
    left, top = max(0, math.floor(x)), max(0, math.floor(y))
    right, bottom = min(image.width, math.ceil(x+w)), min(image.height, math.ceil(y+h))
    if right <= left or bottom <= top:
        return np.asarray([1.], dtype=np.float32)
    # Bound sampling work even for a page-sized or accidentally huge text box.
    size = (min(right-left, 256), min(bottom-top, 256))
    crop = image.resize(size, Image.Resampling.BILINEAR, box=(left, top, right, bottom)).convert("RGB")
    luminance = _linear_luminance(np.asarray(crop))
    if polygon:
        selection = Image.new("L", size)
        ImageDraw.Draw(selection).polygon([
            ((px-left)*size[0]/(right-left), (py-top)*size[1]/(bottom-top)) for px, py in polygon], fill=255)
        selected = luminance[np.asarray(selection) > 0]
        if selected.size:
            return selected
    return luminance.ravel()


def auto_paint(image, region, font_size, bbox):
    luminance = background_luminance(image, region, bbox)
    median = float(np.median(luminance))
    black_score, white_score = (median+.05)/.05, 1.05/(median+.05)
    fill = WHITE if white_score > black_score else BLACK
    hint = ((getattr(region, "layout_data", None) or {}).get("text_paint") or {}).get("source_fill")
    if hint in {BLACK, WHITE}:
        contrast = (luminance+.05)/.05 if hint == BLACK else 1.05/(luminance+.05)
        # Preserve an evidenced source polarity when it is still readable on
        # the repaired background, rather than trusting artwork in the crop.
        if float(np.quantile(contrast, .2)) >= 4.5:
            fill = hint
    contrast = (luminance+.05)/.05 if fill == BLACK else 1.05/(luminance+.05)
    complex_background = (float(np.mean(luminance < .12)) >= .15 and float(np.mean(luminance > .5)) >= .15)
    outlined = complex_background or float(np.quantile(contrast, .2)) < 4.5
    return {"text_color": fill, "stroke_color": WHITE if fill == BLACK else BLACK,
            "stroke_width": max(1, min(3, round(font_size*.06))) if outlined else 0,
            "auto_contrast": True, "background_luminance": round(median, 4),
            "contrast_ratio": round(float(np.median(contrast)), 3), "method": "black_white_background_contrast"}

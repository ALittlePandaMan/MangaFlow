"""Bounded binary-mask transport for the isolated RF-DETR runtime.

Packed, compressed crops avoid full-page JSON pixel arrays and never accept
worker-supplied filesystem paths. No pickle or executable serialization.
"""
from __future__ import annotations

import base64
import zlib
from typing import Any

import numpy as np
from app.services.detection.text_roles import CLASS_NAMES, LayoutPrediction

MAX_PAGE_PIXELS = 32_000_000
MAX_TOTAL_MASK_PIXELS = 96_000_000


def encode_predictions(predictions: list[LayoutPrediction], width: int, height: int) -> dict[str, Any]:
    values = []
    for item in predictions:
        mask = np.asarray(item.mask, dtype=np.uint8)
        values.append({
            "label": item.label, "confidence": item.confidence, "bbox": item.bbox,
            "origin": list(item.mask_origin), "size": [mask.shape[1], mask.shape[0]],
            "mask": base64.b64encode(zlib.compress(np.packbits(mask > 0).tobytes(), 1)).decode("ascii"),
        })
    return {"image_size": [width, height], "detections": values}


def decode_predictions(response: dict[str, Any]) -> tuple[int, int, list[LayoutPrediction]]:
    size = response.get("image_size")
    if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v <= 0 for v in size):
        raise ValueError("invalid_layout_image_size")
    width, height = size
    if width * height > MAX_PAGE_PIXELS:
        raise ValueError("layout_image_too_large")
    values = response.get("detections")
    if not isinstance(values, list) or len(values) > 200:
        raise ValueError("invalid_layout_detections")
    predictions, total = [], 0
    for item in values:
        if not isinstance(item, dict) or item.get("label") not in CLASS_NAMES:
            raise ValueError("invalid_layout_label")
        confidence, box = item.get("confidence"), item.get("bbox")
        if (not isinstance(confidence, (int, float)) or not np.isfinite(confidence) or not 0 <= confidence <= 1
                or not isinstance(box, list) or len(box) != 4 or not np.isfinite(box).all()
                or box[2] <= 0 or box[3] <= 0):
            raise ValueError("invalid_layout_box")
        origin, shape = item.get("origin"), item.get("size")
        if (not isinstance(origin, list) or not isinstance(shape, list) or len(origin) != 2 or len(shape) != 2
                or any(type(v) is not int for v in [*origin, *shape])):
            raise ValueError("invalid_layout_mask_geometry")
        x, y = origin
        w, h = shape
        total += w * h
        if min(x, y) < 0 or min(w, h) <= 0 or x+w > width or y+h > height or total > MAX_TOTAL_MASK_PIXELS:
            raise ValueError("layout_mask_out_of_bounds")
        expected = (w*h+7)//8
        decoder = zlib.decompressobj()
        compressed = base64.b64decode(item["mask"], validate=True)
        packed = decoder.decompress(compressed, expected+1)
        if len(packed) != expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("invalid_layout_mask_payload")
        mask = np.unpackbits(np.frombuffer(packed, np.uint8), count=w*h).reshape(h, w)
        predictions.append(LayoutPrediction(item["label"], float(confidence), list(map(float, box)), mask, (x, y)))
    return width, height, predictions

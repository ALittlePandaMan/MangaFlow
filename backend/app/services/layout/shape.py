"""Exact inscribed text bounds, rather than an area-ratio approximation."""

from __future__ import annotations

import cv2
import numpy as np


def inscribed_text_box(mask: np.ndarray, bbox: list[float], margin: int = 2) -> list[float] | None:
    if mask.ndim != 2 or len(bbox) != 4 or not np.isfinite(bbox).all():
        raise ValueError("Invalid layout shape")
    x, y, width, height = bbox
    left, top = max(0, int(np.ceil(x))), max(0, int(np.ceil(y)))
    right, bottom = min(mask.shape[1], int(x + width)), min(mask.shape[0], int(y + height))
    if left >= right or top >= bottom:
        return None
    binary = (mask > 0).astype(np.uint8)
    if margin:
        binary = cv2.erode(
            binary, np.ones((margin * 2 + 1, margin * 2 + 1), np.uint8), borderType=cv2.BORDER_CONSTANT, borderValue=0
        )
    usable = binary[top:bottom, left:right]
    heights = np.zeros(usable.shape[1], dtype=int)
    best = None
    best_score = (-1, float("-inf"))
    for row, pixels in enumerate(usable):
        heights = (heights + 1) * pixels
        stack = []
        for column in range(len(heights) + 1):
            value = int(heights[column]) if column < len(heights) else 0
            start = column
            while stack and stack[-1][1] > value:
                start, h = stack.pop()
                w = column - start
                center_distance = abs(start + w / 2 - usable.shape[1] / 2) + abs(row + 1 - h / 2 - usable.shape[0] / 2)
                score = (w * h, -center_distance)
                if h >= 8 and w >= 8 and score > best_score:
                    best = [float(left + start), float(top + row + 1 - h), float(w), float(h)]
                    best_score = score
            if not stack or stack[-1][1] < value:
                stack.append((start, value))
    return best

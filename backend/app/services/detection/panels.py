"""Conservative panel-aware order; uncertain/borderless art keeps legacy order."""

from __future__ import annotations

import cv2
import numpy as np
from app.utils.geometry import reading_order_japanese


def detect_panel_frames(image: np.ndarray) -> list[list[float]]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    height, width = gray.shape
    ink = (gray < 120).astype(np.uint8)
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    candidates = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        if w < width * 0.18 or h < height * 0.1 or not 0.035 <= w * h / (width * height) <= 0.94:
            continue
        if abs(cv2.contourArea(contour)) / (w * h) < 0.75:
            continue
        radius = max(2, round(min(width, height) / 400))
        top = ink[max(0, y - radius) : min(height, y + radius + 1), x : x + w].max(axis=0).mean()
        bottom = ink[max(0, y + h - radius - 1) : min(height, y + h + radius), x : x + w].max(axis=0).mean()
        left = ink[y : y + h, max(0, x - radius) : min(width, x + radius + 1)].max(axis=1).mean()
        right = ink[y : y + h, max(0, x + w - radius - 1) : min(width, x + w + radius)].max(axis=1).mean()
        if min(top, bottom, left, right) >= 0.7:
            candidates.append([float(x), float(y), float(w), float(h)])
    # Keep enclosing panel frames, not their captions or the duplicate inner
    # contour of a thick stroke. Full-page decorative borders were excluded.
    candidates.sort(key=lambda box: box[2] * box[3], reverse=True)
    selected = []
    for box in candidates:
        x, y, w, h = box
        if any(x >= a - 4 and y >= b - 4 and x + w <= a + c + 4 and y + h <= b + d + 4 for a, b, c, d in selected):
            continue
        selected.append(box)
    return selected if len(selected) >= 2 else []


def _panel_order(boxes: list[list[float]], indices: list[int]) -> list[int]:
    if len(indices) < 2:
        return indices
    # Full-width row gutters take precedence; vertical cuts handle staggered
    # layouts with a tall panel alongside a column of shorter panels.
    for axis in (1, 0):
        ordered = sorted(indices, key=lambda i: boxes[i][axis])
        far = boxes[ordered[0]][axis] + boxes[ordered[0]][axis + 2]
        cuts = []
        for offset in range(1, len(ordered)):
            start = boxes[ordered[offset]][axis]
            if start - far >= 2:
                cuts.append((start - far, offset))
            far = max(far, start + boxes[ordered[offset]][axis + 2])
        if cuts:
            _, cut = max(cuts)
            first, second = ordered[:cut], ordered[cut:]
            if axis == 0:
                first, second = second, first
            return _panel_order(boxes, first) + _panel_order(boxes, second)
    return [indices[i] for i in reading_order_japanese([{"bbox": boxes[index]} for index in indices])]


def panel_reading_order(regions: list[dict], panels: list[list[float]]) -> tuple[list[int], dict[int, int]]:
    if not panels:
        return reading_order_japanese(regions), {}
    assignments = {}
    members: dict[int, list[int]] = {}
    for index, region in enumerate(regions):
        x, y, w, h = region["bbox"]
        cx, cy = x + w / 2, y + h / 2
        matches = [p for p, (a, b, c, d) in enumerate(panels) if a <= cx <= a + c and b <= cy <= b + d]
        if len(matches) == 1:
            assignments[index] = matches[0]
            members.setdefault(matches[0], []).append(index)
    # Do not reorder a whole page from a small, uncertain fragment of evidence.
    if len(members) < 2 or len(assignments) < len(regions) * 0.6:
        return reading_order_japanese(regions), {}
    groups = [members[p] for p in sorted(members)]
    boxes = [panels[p] for p in sorted(members)]
    for index, region in enumerate(regions):
        if index not in assignments:
            groups.append([index])
            boxes.append(region["bbox"])
    result = []
    for group_index in _panel_order(boxes, list(range(len(groups)))):
        group = groups[group_index]
        result.extend(group[i] for i in reading_order_japanese([regions[index] for index in group]))
    return result, assignments

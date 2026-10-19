"""Bounded high-resolution CTD crops; no new model or network calls."""
from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

import cv2
import numpy as np
from app.services.inpainting.text_segmentation import text_probability

Window = tuple[int, int, int, int]


def polygon_boxes(polygons: Iterable) -> list[Window]:
    boxes = []
    for polygon in polygons:
        try:
            points = np.asarray(polygon, dtype=np.float64)
        except (TypeError, ValueError):
            continue
        if points.ndim == 2 and points.shape[0] >= 3 and points.shape[1] == 2 and np.isfinite(points).all():
            boxes.append(cv2.boundingRect(np.rint(points).astype(np.int32)))
    return boxes


def inspection_area(shape: tuple[int, int], polygons: Iterable) -> np.ndarray:
    area = np.zeros(shape, np.uint8)
    for polygon in polygons:
        try:
            points = np.asarray(polygon, dtype=np.float64)
        except (TypeError, ValueError):
            continue
        if points.ndim == 2 and points.shape[0] >= 3 and points.shape[1] == 2 and np.isfinite(points).all():
            points = np.clip(points, [0, 0], [shape[1] - 1, shape[0] - 1])
            cv2.fillPoly(area, [np.rint(points).astype(np.int32)], 255)
    # This is inspection-only, not permission to grow the repair mask.
    return cv2.dilate(area, np.ones((9, 9), np.uint8))


def focus_windows(shape: tuple[int, int], boxes: Iterable, *, limit: int = 6) -> list[Window]:
    """Prioritize tiny text, coalesce neighbouring boxes, cap per-page work."""
    height, width = shape
    if limit <= 0 or min(height, width) <= 0:
        return []
    valid = []
    for box in boxes:
        if len(box) != 4 or not np.isfinite(box).all():
            continue
        x, y, w, h = map(float, box)
        if w <= 0 or h <= 0 or x >= width or y >= height or x + w <= 0 or y + h <= 0:
            continue
        x2, y2 = min(width, x+w), min(height, y+h)
        x, y = max(0, x), max(0, y)
        valid.append((x, y, x2-x, y2-y))
    windows: list[Window] = []
    for x, y, w, h in sorted(valid, key=lambda b: (min(b[2:]), b[1], b[0])):
        # A long narrow text line may need more than one tile.
        nx, ny = max(1, int(np.ceil(w / 384))), max(1, int(np.ceil(h / 384)))
        for j in range(ny):
            for i in range(nx):
                cx = min(width - 1, max(0, x + w * (i + .5) / nx))
                cy = min(height - 1, max(0, y + h * (j + .5) / ny))
                if any(wx + 32 <= cx <= wx + ww - 32 and wy + 32 <= cy <= wy + wh - 32
                       for wx, wy, ww, wh in windows):
                    continue
                ww, wh = min(512, width), min(512, height)
                wx = min(width - ww, max(0, int(cx - ww / 2)))
                wy = min(height - wh, max(0, int(cy - wh / 2)))
                candidate = (wx, wy, ww, wh)
                if candidate not in windows:
                    windows.append(candidate)
                if len(windows) >= limit:
                    return windows
    return windows


def refine_probability(
    source: Path, model_dir: Path, probability: np.ndarray | None, boxes: list,
    cache_dir: Path | None = None, *, check: Callable[[], None] = lambda: None, backend: str | None = None,
) -> tuple[np.ndarray | None, dict]:
    if probability is None:
        return None, {"status": "unavailable", "windows": 0}
    # Do not spend local inference on text already large in the whole-page map.
    scale = 1024 / max(probability.shape)
    small = [b for b in boxes if len(b) == 4 and 0 < min(b[2:]) * scale < 24]
    windows = focus_windows(probability.shape, small)
    if not windows:
        return probability, {"status": "not_needed", "windows": 0}
    allowed = np.zeros(probability.shape, np.uint8)
    for x, y, w, h in small:
        x1, y1 = max(0, int(x) - 4), max(0, int(y) - 4)
        x2, y2 = min(allowed.shape[1], int(np.ceil(x+w)) + 4), min(allowed.shape[0], int(np.ceil(y+h)) + 4)
        if x2 > x1 and y2 > y1:
            allowed[y1:y2, x1:x2] = 255
    merged = probability.copy()
    used = 0
    for window in windows:
        check()
        local, _ = text_probability(source, model_dir, cache_dir, window=window, backend=backend)
        if local is None:
            continue
        x, y, w, h = window
        roi = np.s_[y:y+h, x:x+w]
        merged[roi] = np.where(allowed[roi] > 0, np.maximum(merged[roi], local), merged[roi])
        used += 1
    return merged, {"status": "ready", "windows": used, "window_limit": 6, "scope": "small_text_boxes"}


class LocalResidualConfirmer:
    """Reuse fixed crops on first pass and retry for comparable model evidence.

    Agreement authorizes only existing-mask retries. Disagreement is uncertainty,
    never proof that the first result is clean. Crop limits remain visible.
    """
    def __init__(self, source: Path, model_dir: Path, cache_dir: Path | None = None,
                 check: Callable[[], None] = lambda: None, *, backend: str | None = None):
        self.source, self.model_dir, self.cache_dir, self.check = source, model_dir, cache_dir, check
        self.backend = backend
        self.windows: list[Window] | None = None
        self.before: dict[Window, np.ndarray | None] = {}

    def __call__(self, output: Path, candidates: np.ndarray) -> np.ndarray:
        if self.windows is None:
            _, _, stats, _ = cv2.connectedComponentsWithStats((candidates > 0).astype(np.uint8), connectivity=8)
            self.windows = focus_windows(candidates.shape, stats[1:, :4])
        confirmed = np.zeros_like(candidates)
        for window in self.windows:
            self.check()
            if window not in self.before:
                self.before[window] = text_probability(self.source, self.model_dir, self.cache_dir, window=window, backend=self.backend)[0]
            before = self.before[window]
            after, _ = text_probability(output, self.model_dir, self.cache_dir, window=window, backend=self.backend)
            if before is None or after is None:
                continue
            x, y, w, h = window
            roi = np.s_[y:y+h, x:x+w]
            confirmed[roi] |= np.where((before >= .4) & (after >= .65) & (candidates[roi] > 0), 255, 0).astype(np.uint8)
        return confirmed

"""Independent OpenCV adapter for the separately distributed CTD ONNX model.

No upstream Python code is bundled. Model origin/license and installation are
documented in docs/translation-quality.md. Inference never downloads weights.
"""

from __future__ import annotations

import hashlib
import threading
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np

MODEL_FILENAME = "text-segmentation/comictextdetector.pt.onnx"
MODEL_URL = "https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.2.1/comictextdetector.pt.onnx"
MODEL_SHA256 = "1a86ace74961413cbd650002e7bb4dcec4980ffa21b2f19b86933372071d718f"
MODEL_BYTES = 94669756


class ComicTextSegmenter:
    def __init__(self, path: Path):
        if hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("Text segmentation model checksum mismatch")
        self.net = cv2.dnn.readNetFromONNX(str(path))
        self.lock = threading.RLock()

    def predict(self, image: np.ndarray) -> np.ndarray:
        height, width = image.shape[:2]
        if not height or not width:
            raise ValueError("Empty segmentation image")
        scale = 1024 / max(height, width)
        h, w = max(1, round(height * scale)), max(1, round(width * scale))
        # This export expects RGB, unit range, and padding only at right/bottom.
        canvas = np.zeros((1024, 1024, 3), np.uint8)
        canvas[:h, :w] = cv2.resize(image, (w, h), interpolation=cv2.INTER_LINEAR)
        blob = cv2.dnn.blobFromImage(canvas, 1 / 255.0, swapRB=True)
        with self.lock:
            self.net.setInput(blob)
            outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())
        candidates = [v for v in outputs if v.ndim == 4 and v.shape[:2] == (1, 1)]
        if len(candidates) != 1:
            raise ValueError("Unsupported CTD segmentation output")
        probability = candidates[0][0, 0]
        if probability.shape != (1024, 1024) or not np.isfinite(probability).all():
            raise ValueError("Invalid CTD probability map")
        return np.clip(cv2.resize(probability[:h, :w], (width, height)), 0, 1)


@lru_cache(maxsize=2)
def _segmenter(path: str, size: int, modified: int) -> ComicTextSegmenter:
    return ComicTextSegmenter(Path(path))


def text_probability(
    source: Path, model_dir: Path, cache_dir: Path | None = None,
    *, window: tuple[int, int, int, int] | None = None,
) -> tuple[np.ndarray | None, dict]:
    path = model_dir / MODEL_FILENAME
    if not path.is_file():
        return None, {"status": "unavailable", "reason": "model_missing", "model": "comic-text-detector"}
    image = cv2.imread(str(source))
    if image is None:
        raise ValueError("Cannot decode text segmentation source")
    if window is not None:
        x, y, w, h = window
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > image.shape[1] or y + h > image.shape[0]:
            raise ValueError("Invalid local segmentation window")
        image = image[y:y+h, x:x+w]
    stat = path.stat()
    segmenter = _segmenter(str(path.resolve()), stat.st_size, stat.st_mtime_ns)
    # Cache keys bind both image contents and verified weights, not a mutable page ID.
    key = hashlib.sha256(source.read_bytes() + MODEL_SHA256.encode() + str(window).encode()).hexdigest()
    target = cache_dir / f"{key}.png" if cache_dir is not None else None
    if target is not None and target.is_file():
        cached = cv2.imread(str(target), cv2.IMREAD_UNCHANGED)
        if cached is not None and cached.dtype == np.uint16 and cached.shape == image.shape[:2]:
            return cached.astype(np.float32) / 65535, {
                "status": "ready",
                "model": "comic-text-detector",
                "cached": True,
            }
    probability = segmenter.predict(image)
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.stem}-{uuid4().hex}.png")
        try:
            if not cv2.imwrite(str(temporary), np.rint(probability * 65535).astype(np.uint16)):
                raise OSError("Cannot save segmentation cache")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return probability, {"status": "ready", "model": "comic-text-detector", "cached": False}


def learned_text_mask(
    probability: np.ndarray, region: np.ndarray, *, expand: int = 2, allow_outside: bool = False
) -> np.ndarray:
    """Hysteresis and polygon anchoring reject isolated artwork predictions."""
    if probability.shape != region.shape or not np.isfinite(probability).all():
        raise ValueError("Text probability dimensions/values are invalid")
    radius = max(0, min(6, int(expand)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    search = cv2.dilate(region, kernel) if allow_outside else region
    weak = ((probability >= 0.25) & (search > 0)).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(weak, connectivity=8)
    seeds = np.bincount(labels[(probability >= 0.65) & (region > 0)], minlength=n)
    selected = np.zeros(n, np.uint8)
    for label in range(1, n):
        if seeds[label] >= 2 and stats[label, cv2.CC_STAT_AREA] >= 3:
            selected[label] = 255
    glyphs = selected[labels]
    # A wildly overconfident map is not permission to erase a whole rectangle.
    if cv2.countNonZero(glyphs) > cv2.countNonZero(search) * 0.8:
        return np.zeros_like(region)
    return cv2.bitwise_and(cv2.dilate(glyphs, kernel), search)

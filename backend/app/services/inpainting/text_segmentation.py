"""Independent FP32 adapters for the separately distributed CTD ONNX model.

No upstream Python code is bundled. Model origin/license and installation are
documented in docs/translation-quality.md. Inference never downloads weights.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from app.core.config import get_settings
from app.core.timing import measured, record_timing

MODEL_FILENAME = "text-segmentation/comictextdetector.pt.onnx"
MODEL_URL = "https://github.com/zyddnys/manga-image-translator/releases/download/beta-0.2.1/comictextdetector.pt.onnx"
MODEL_SHA256 = "1a86ace74961413cbd650002e7bb4dcec4980ffa21b2f19b86933372071d718f"
MODEL_BYTES = 94669756
SOURCE_CACHE_BYTES = 64 * 1024**2
_sources: OrderedDict[tuple, tuple[np.ndarray, object]] = OrderedDict()
_source_bytes = 0
_cache_lock = threading.Lock()
_flights: dict[tuple, list] = {}
logger = logging.getLogger(__name__)


@contextmanager
def _single_flight(key: tuple):
    """Serialize only equal cache misses; remove keys after all waiters drain."""
    with _cache_lock:
        entry = _flights.setdefault(key, [threading.Lock(), 0])
        entry[1] += 1
    waiting = time.monotonic()
    try:
        with entry[0]:
            record_timing("segmentation.coalescing_wait", time.monotonic() - waiting)
            yield
    finally:
        with _cache_lock:
            entry[1] -= 1
            if not entry[1]:
                del _flights[key]


def _signature(path: Path) -> tuple:
    stat = path.stat()
    return (str(path.resolve()), stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns, stat.st_ino)


@measured("segmentation.source")
def _source_image(source: Path):
    """Decode/hash a page once across local windows; bound retained RGB memory.

    The digest is copied before adding model/window bytes, preserving existing
    disk-cache keys exactly. Read-only pixels prevent cross-worker corruption.
    """
    global _source_bytes
    key = _signature(source)
    with _single_flight(("source", *key)):
        with _cache_lock:
            cached = _sources.get(key)
            if cached is not None:
                _sources.move_to_end(key)
                return cached
        data = source.read_bytes()
        image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Cannot decode text segmentation source")
        if _signature(source) != key:
            raise ValueError("Text segmentation source changed while reading")
        image.setflags(write=False)
        result = image, hashlib.sha256(data)
        with _cache_lock:
            # Drop old versions of a mutable path, not just LRU entries.
            for previous in [item for item in _sources if item[0] == key[0]]:
                _source_bytes -= _sources.pop(previous)[0].nbytes
            if image.nbytes <= SOURCE_CACHE_BYTES:
                while _sources and _source_bytes + image.nbytes > SOURCE_CACHE_BYTES:
                    _source_bytes -= _sources.popitem(last=False)[1][0].nbytes
                _sources[key] = result
                _source_bytes += image.nbytes
        return result


class ComicTextSegmenter:
    def __init__(self, path: Path, *, backend: str | None = None):
        if hashlib.sha256(path.read_bytes()).hexdigest() != MODEL_SHA256:
            raise ValueError("Text segmentation model checksum mismatch")
        self.lock = threading.RLock()
        self.path = path
        self.net = None
        self.cuda = None
        self.fallback_reason = None
        self.backend = "opencv"
        selected = backend or get_settings().segmentation_backend
        if selected not in {"auto", "opencv", "onnx_cuda"}:
            raise ValueError("Unsupported segmentation backend")
        if selected in {"auto", "onnx_cuda"}:
            try:
                from app.services.execution import execution_runtime
                from app.services.inpainting.segmentation_cuda import CudaSegmentationSession

                with execution_runtime().model_slot():
                    self.cuda = CudaSegmentationSession(path, get_settings().native_threads)
                self.backend = "onnx_cuda"
            except Exception as error:
                self.fallback_reason = getattr(error,"reason","cuda_initialization_failed")
                logger.warning("CTD CUDA unavailable; using OpenCV: %s: %s", type(error).__name__, error)
        self._report()

    def _report(self):
        from app.services.execution import execution_runtime

        execution_runtime().report_inference(kind="segmentation", name="comic-text-detector",
            backend=self.backend, device="cuda:0" if self.backend == "onnx_cuda" else "cpu",
            state="ready", fallback_reason=self.fallback_reason)

    def _forward(self, blob):
        if self.cuda is not None:
            try:
                from app.services.execution import execution_runtime

                with execution_runtime().model_slot():
                    outputs = self.cuda.forward(blob)
                    _probability_map(outputs)
                    return outputs
            except Exception as error:
                # Free the failed session before loading the CPU network. Do
                # not silently retry a CUDA session after each local window.
                self.cuda = None
                self.backend = "opencv"
                self.fallback_reason = getattr(error,"reason","cuda_inference_failed")
                self._report()
                logger.warning("CTD CUDA failed; using OpenCV: %s: %s", type(error).__name__, error)
        if self.net is None:
            self.net = cv2.dnn.readNetFromONNX(str(self.path))
        self.net.setInput(blob)
        return self.net.forward(self.net.getUnconnectedOutLayersNames())

    @measured("segmentation.predict")
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
        waiting = time.monotonic()
        with self.lock:
            record_timing("segmentation.model_wait", time.monotonic() - waiting)
            started = time.monotonic()
            try:
                outputs = self._forward(blob)
            finally:
                record_timing("segmentation.model_compute", time.monotonic() - started)
        probability = _probability_map(outputs)
        return np.clip(cv2.resize(probability[:h, :w], (width, height)), 0, 1)


def _probability_map(outputs):
    candidates = [value for value in outputs if value.ndim == 4 and value.shape[:2] == (1,1)]
    if len(candidates) != 1:
        raise ValueError("Unsupported CTD segmentation output")
    probability = candidates[0][0,0]
    if probability.shape != (1024,1024) or not np.isfinite(probability).all():
        raise ValueError("Invalid CTD probability map")
    return probability


@lru_cache(maxsize=2)
def _segmenter(path: str, size: int, modified: int, backend: str = "auto") -> ComicTextSegmenter:
    return ComicTextSegmenter(Path(path), backend=backend)


def text_probability(
    source: Path, model_dir: Path, cache_dir: Path | None = None,
    *, window: tuple[int, int, int, int] | None = None, backend: str | None = None,
) -> tuple[np.ndarray | None, dict]:
    path = model_dir / MODEL_FILENAME
    if not path.is_file():
        return None, {"status": "unavailable", "reason": "model_missing", "model": "comic-text-detector"}
    image, digest = _source_image(source)
    if window is not None:
        x, y, w, h = window
        if min(x, y) < 0 or min(w, h) <= 0 or x + w > image.shape[1] or y + h > image.shape[0]:
            raise ValueError("Invalid local segmentation window")
        image = image[y:y+h, x:x+w]
    stat = path.stat()
    selected_backend = backend or get_settings().segmentation_backend
    if selected_backend not in {"auto", "opencv", "onnx_cuda"}:
        raise ValueError("Unsupported segmentation backend")
    model_key = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, selected_backend)
    # functools.lru_cache alone may initialize the same large Net repeatedly
    # when the first batch of pages arrives concurrently.
    with _single_flight(("model", *model_key)):
        segmenter = _segmenter(*model_key)
    # Cache keys bind both image contents and verified weights, not a mutable page ID.
    key_digest = digest.copy()
    key_digest.update(MODEL_SHA256.encode() + str(window).encode())
    # Preserve legacy OpenCV cache keys. CUDA output gets its own namespace;
    # comparison and rollback must never reuse a map from another backend.
    if getattr(segmenter, "backend", "opencv") == "onnx_cuda":
        key_digest.update(b":onnx_cuda_fp32_v1")
    key = key_digest.hexdigest()
    target = cache_dir / f"{key}.png" if cache_dir is not None else None
    if target is not None:
        with _single_flight(("probability", str(target.resolve()))):
            return _cached_probability(segmenter, image, target)
    return _cached_probability(segmenter, image, None)


def _cached_probability(segmenter, image: np.ndarray, target: Path | None):
    metadata = {"backend": getattr(segmenter, "backend", "opencv")}
    if getattr(segmenter, "fallback_reason", None):
        metadata["fallback_reason"] = segmenter.fallback_reason
    if target is not None and target.is_file():
        cached = cv2.imread(str(target), cv2.IMREAD_UNCHANGED)
        if cached is not None and cached.dtype == np.uint16 and cached.shape == image.shape[:2]:
            return cached.astype(np.float32) / 65535, {
                "status": "ready",
                "model": "comic-text-detector",
                "cached": True,
                **metadata,
            }
    probability = segmenter.predict(image)
    # Inference can change backend after an accelerator failure. Never save a
    # fallback result under a CUDA cache key.
    if metadata["backend"] != getattr(segmenter, "backend", "opencv"):
        target = None
    metadata["backend"] = getattr(segmenter, "backend", "opencv")
    if getattr(segmenter, "fallback_reason", None):
        metadata["fallback_reason"] = segmenter.fallback_reason
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.stem}-{uuid4().hex}.png")
        try:
            if not cv2.imwrite(str(temporary), np.rint(probability * 65535).astype(np.uint16)):
                raise OSError("Cannot save segmentation cache")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    return probability, {"status": "ready", "model": "comic-text-detector", "cached": False, **metadata}


def learned_text_mask(
    probability: np.ndarray, region: np.ndarray, *, search_margin: int = 0
) -> np.ndarray:
    """Keep seeded glyph pixels without dilation; a search margin only finds ink."""
    if probability.shape != region.shape or not np.isfinite(probability).all():
        raise ValueError("Text probability dimensions/values are invalid")
    radius = max(0, min(6, int(search_margin)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (radius * 2 + 1, radius * 2 + 1))
    search = cv2.dilate(region, kernel) if radius else region
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
    return cv2.bitwise_and(glyphs, search)

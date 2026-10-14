from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from app.services.base import ProviderError


def load_paddle_image(image_path: Path) -> np.ndarray:
    """Decode formats accepted by uploads into contiguous, uint8 BGR pixels.

    PaddleX's path sampler rejects WebP before inference. Decode with Pillow
    instead of converting originals on disk, retaining native page coordinates.
    """
    try:
        with Image.open(image_path) as source:
            if "A" in source.getbands() or "transparency" in source.info:
                rgba = source.convert("RGBA")
                white = Image.new("RGBA", source.size, (255, 255, 255, 255))
                rgb = Image.alpha_composite(white, rgba).convert("RGB")
            else:
                rgb = source.convert("RGB")
            return np.ascontiguousarray(np.asarray(rgb, dtype=np.uint8)[..., ::-1])
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ProviderError(f"Cannot decode image for PaddleOCR: {image_path.name}: {exc}") from exc


def predict_paddle_image(model: Any, image: np.ndarray) -> list[dict[str, Any]]:
    """Consume lazy inference inside the caller's GPU retry/locking boundary.

    An empty *page* response is an input/inference failure, not proof that the
    page has no text. A valid response containing zero polygons is legitimate.
    """
    predicted = model.predict(image)
    if predicted is None:
        pages = []
    elif isinstance(predicted, (dict, list, tuple)) or hasattr(predicted, "json"):
        pages = list(predicted) if isinstance(predicted, (list, tuple)) else [predicted]
    else:
        pages = list(predicted)
    if not pages:
        raise ProviderError("PaddleOCR produced no page result; image input was not processed")
    payloads = []
    for page in pages:
        payload = _result_payload(page)
        if payload.get("error") or not any(key in payload for key in ("dt_polys", "rec_polys")):
            raise ProviderError("PaddleOCR returned an invalid page result; image input was not processed")
        payloads.append(payload)
    return payloads


def _plain(value: Any) -> Any:
    """Convert Paddle/PaddleX result values (often numpy arrays) to plain Python values."""
    return value.tolist() if hasattr(value, "tolist") else value


def _result_payload(result: Any) -> dict[str, Any]:
    raw = getattr(result, "json", result)
    if callable(raw):
        raw = raw()
    if not isinstance(raw, dict):
        return {}
    nested = raw.get("res")
    return nested if isinstance(nested, dict) else raw


def extract_paddle_lines(results: Any) -> list[dict[str, Any]]:
    """Normalize PaddleOCR 3.x results without leaking PaddleX objects into the pipeline."""
    if results is None:
        return []
    if not isinstance(results, (list, tuple)):
        results = [results]
    output: list[dict[str, Any]] = []
    for result in results:
        payload = _result_payload(result)
        polygons = payload.get("rec_polys")
        if polygons is None:
            polygons = payload.get("dt_polys")
        texts = payload.get("rec_texts")
        scores = payload.get("rec_scores")
        if scores is None:
            scores = payload.get("dt_scores")
        polygons = _plain(polygons) or []
        texts = _plain(texts) or []
        scores = _plain(scores) or []
        count = max(len(polygons), len(texts), len(scores))
        for index in range(count):
            polygon = _plain(polygons[index]) if index < len(polygons) else []
            text = str(texts[index]).strip() if index < len(texts) else ""
            try:
                score = float(scores[index]) if index < len(scores) else 0.0
            except (TypeError, ValueError):
                score = 0.0
            output.append({"polygon": polygon, "text": text, "confidence": score})
    return output

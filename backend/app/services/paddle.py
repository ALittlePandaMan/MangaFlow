from __future__ import annotations

from typing import Any


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

"""Conservative text/SFX matching, independent of the inference runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import cv2
import numpy as np

CLASSIFICATION_VERSION = 1
CLASS_NAMES = ("text", "onomatopoeia", "bubble", "panel")
DEFAULT_SFX_CONFIDENCE = 0.60


@dataclass(slots=True)
class LayoutPrediction:
    label: str
    confidence: float
    bbox: list[float]
    mask: np.ndarray
    mask_origin: tuple[int, int]


def classification_options(config: dict[str, Any]) -> dict[str, Any]:
    value = config.get("text_classification")
    if value is False:
        return {"enabled": False}
    options = dict(value) if isinstance(value, dict) else {}
    return {"enabled": True, "filter_sfx": True, "min_sfx_confidence": DEFAULT_SFX_CONFIDENCE, **options}


def excluded_as_sfx(region: Any) -> bool:
    data = region.metadata.get("text_classification", {})
    return (
        isinstance(data, dict)
        and data.get("status") == "classified"
        and data.get("label") == "onomatopoeia"
        and data.get("excluded") is True
    )


def _coverage(polygon: list[list[float]], prediction: LayoutPrediction) -> tuple[float, float]:
    points = np.asarray(polygon, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3 or not np.isfinite(points).all():
        return 0.0, 0.0
    x, y, width, height = prediction.bbox
    if not np.isfinite(prediction.bbox).all() or width <= 0 or height <= 0:
        return 0.0, 0.0
    area = abs(cv2.contourArea(points.astype(np.float32)))
    if area < 1:
        return 0.0, 0.0
    box = np.asarray([[x, y], [x + width, y], [x + width, y + height], [x, y + height]], np.float32)
    # Detector quadrilaterals are convex; use their hull for invalid winding.
    hull = cv2.convexHull(points.astype(np.float32))
    intersection, _ = cv2.intersectConvexConvex(hull, box)
    coverage = min(1.0, max(0.0, float(intersection) / area))
    if coverage < 0.25:
        return coverage, 0.0
    mask = prediction.mask
    if mask.ndim != 2 or not mask.size:
        return coverage, 0.0
    local = points - np.asarray(prediction.mask_origin)
    left, top = np.maximum(0, np.floor(local.min(axis=0)).astype(int))
    right, bottom = np.minimum([mask.shape[1], mask.shape[0]], np.ceil(local.max(axis=0)).astype(int) + 1)
    if left >= right or top >= bottom:
        return coverage, 0.0
    region_mask = np.zeros((bottom - top, right - left), np.uint8)
    cv2.fillPoly(region_mask, [np.rint(local - [left, top]).astype(np.int32)], 1)
    ink = np.count_nonzero((mask[top:bottom, left:right] > 0) & (region_mask > 0))
    return coverage, min(1.0, float(ink) / area)


def classify_text_polygons(
    polygons: list[list[list[float]]],
    predictions: list[LayoutPrediction],
    *,
    min_sfx_confidence: float = DEFAULT_SFX_CONFIDENCE,
    filter_sfx: bool = True,
    protected_indices: set[int] | None = None,
) -> list[dict[str, Any]]:
    if not np.isfinite(min_sfx_confidence) or not 0.4 <= min_sfx_confidence <= 0.95:
        raise ValueError("SFX confidence must be between 0.4 and 0.95")
    output = []
    for index, polygon in enumerate(polygons):
        matches = []
        for prediction in predictions:
            if not np.isfinite(prediction.confidence) or not 0 <= prediction.confidence <= 1:
                continue
            coverage, ink = _coverage(polygon, prediction)
            matches.append((prediction, coverage, ink))
        texts = [p for p, coverage, ink in matches if p.label == "text" and p.confidence >= 0.25 and coverage >= 0.3 and ink >= 0.02]
        sfx = [p for p, coverage, ink in matches if p.label == "onomatopoeia" and p.confidence >= min_sfx_confidence and coverage >= 0.75 and ink >= 0.025]
        protected = index in (protected_indices or set()) or any(
            p.label == "bubble" and p.confidence >= 0.75 and coverage >= 0.6 and ink >= 0.4
            for p, coverage, ink in matches
        )
        data: dict[str, Any] = {"version": CLASSIFICATION_VERSION, "label": "uncertain", "status": "uncertain", "excluded": False}
        if sfx and not texts:
            winner = max(sfx, key=lambda p: p.confidence)
            data.update(label="onomatopoeia", status="classified", confidence=round(winner.confidence, 4), excluded=bool(filter_sfx and not protected))
            if protected:
                data["reason"] = "bubble_dialogue_protection"
        elif sfx and texts:
            data["reason"] = "conflicting_text_and_sfx"
        elif texts:
            winner = max(texts, key=lambda p: p.confidence)
            data.update(label="text", status="classified", confidence=round(winner.confidence, 4))
        else:
            data["reason"] = "no_reliable_classification"
        output.append(data)
    return output


def partition_text_roles(regions: list[Any]) -> tuple[list[Any], dict[str, Any]]:
    """Excluded candidates never become TextRegions, OCR inputs or repair masks."""
    excluded = [region for region in regions if excluded_as_sfx(region)]
    kept = [region for region in regions if not excluded_as_sfx(region)]
    classifications = [region.metadata.get("text_classification", {}) for region in regions]
    statuses = {item.get("status", "unavailable") for item in classifications}
    status = "completed" if statuses & {"classified", "uncertain"} else next(iter(statuses), "not_run")
    reasons = sorted({str(item["reason"]) for item in classifications if item.get("status") == "unavailable" and item.get("reason")})
    report = {
        "version": CLASSIFICATION_VERSION,
        "status": status,
        "kept_count": len(kept),
        "excluded_count": len(excluded),
        "uncertain_count": sum(item.get("label") == "uncertain" for item in classifications),
        "excluded_candidates": [
            {"polygon": region.polygon, "bbox": region.bbox, "classification": region.metadata["text_classification"]}
            for region in excluded
        ],
    }
    if reasons:
        report["unavailable_reasons"] = reasons
    policies = [item.get("policy") for item in classifications if isinstance(item.get("policy"), dict)]
    if policies and all(policy == policies[0] for policy in policies):
        report["policy"] = policies[0]
    return kept, report

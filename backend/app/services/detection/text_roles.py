"""Native RF-DETR prediction data and reversible text/SFX reporting."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

CLASSIFICATION_VERSION = 1
CLASS_NAMES = ("text", "onomatopoeia", "bubble", "panel")


@dataclass(slots=True)
class LayoutPrediction:
    label: str
    confidence: float
    bbox: list[float]
    mask: np.ndarray
    mask_origin: tuple[int, int]


def is_confirmed_free_text(metadata: Any, bubble_id: str | None = None) -> bool:
    """Unassigned alone means unknown; require authoritative role provenance."""
    if bubble_id or not isinstance(metadata, dict) or metadata.get("detector") != "rfdetr":
        return False
    role = metadata.get("text_role", {})
    classification = metadata.get("text_classification", {})
    assignment = metadata.get("balloon_assignment", {})
    return (
        isinstance(role, dict) and role.get("label") == "free_text"
        and isinstance(classification, dict) and classification.get("status") == "classified"
        and classification.get("label") == "text" and classification.get("excluded") is False
        and isinstance(assignment, dict) and assignment.get("status") == "unassigned"
        and assignment.get("bubble_id") is None and assignment.get("method") == "text_mask_containment"
    )


def excluded_as_sfx(region: Any) -> bool:
    data = region.metadata.get("text_classification", {})
    return (
        isinstance(data, dict)
        and data.get("status") == "classified"
        and data.get("label") == "onomatopoeia"
        and data.get("excluded") is True
    )


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

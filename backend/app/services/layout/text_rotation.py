"""One-time typography initialization, independent of editable frame geometry."""
from __future__ import annotations

import math

from app.services.detection.text_roles import is_confirmed_free_text

TEXT_ROTATION_KEY = "text_rotation"


def effective_text_rotation(region, *, perspective: bool | None = None) -> float:
    """Perspective supplies the detector angle; only user angles are additive.

    Keep the stored initialization intact so switching back to standard text
    restores it, without mutating geometry, user intent or undo history.
    """
    if perspective is None:
        perspective = bool(getattr(region, "perspective_warp", False))
    provenance = (region.layout_data or {}).get(TEXT_ROTATION_KEY)
    if perspective and isinstance(provenance, dict):
        angle = provenance.get("angle")
        automatic = (provenance.get("source") == "detector" and provenance.get("role") == "free_text"
                     or provenance.get("version") == 2 and provenance.get("source") in {"detector", "segmentation"})
        if (automatic
                and not isinstance(angle, bool) and isinstance(angle, (float, int))
                and math.isfinite(angle) and abs(angle-region.rotation) <= .01):
            return 0.
    return region.rotation


def free_text_angle(metadata: dict, bubble_id: str | None = None) -> float | None:
    """Missing association is unknown, never sufficient proof of free text."""
    if not is_confirmed_free_text(metadata, bubble_id):
        return None
    value = metadata.get("text_angle")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    angle = float(value)
    return angle if math.isfinite(angle) and abs(angle) <= 45 else None


def can_initialize_rotation(region) -> bool:
    layout = region.layout_data or {}
    detection = layout.get("detection", {})
    if isinstance(detection, dict):
        role = detection.get("text_role", {})
        assignment = detection.get("balloon_assignment", {})
        if ((isinstance(role, dict) and role.get("label") == "dialogue")
                or (isinstance(assignment, dict) and assignment.get("status") == "assigned")):
            return False
    # A user-authored zero is just as intentional as a nonzero angle. An
    # initialized angle is not a live link to later frame manipulation.
    if TEXT_ROTATION_KEY in layout or region.locked or region.perspective_warp or region.bubble_id:
        return False
    if not math.isfinite(region.rotation) or abs(region.rotation) > .01:
        return False
    return ((not region.translated_polygon or region.translated_polygon == region.polygon)
            and (not region.translated_bbox or region.translated_bbox == region.bbox))


def initialize_free_text_rotation(region, metadata: dict | None = None) -> bool:
    if not can_initialize_rotation(region):
        return False
    evidence = metadata if metadata is not None else (region.layout_data or {}).get("detection", {})
    angle = free_text_angle(evidence, region.bubble_id)
    if angle is None:
        return False
    region.rotation = angle
    region.layout_data = {**(region.layout_data or {}), TEXT_ROTATION_KEY: {
        "version": 1, "source": "detector", "angle": angle, "role": "free_text",
    }}
    return True


def record_user_rotation(region) -> None:
    region.layout_data = {**(region.layout_data or {}), TEXT_ROTATION_KEY: {
        "version": 1, "source": "user", "angle": region.rotation,
    }}

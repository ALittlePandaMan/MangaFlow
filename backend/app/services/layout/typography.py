"""Shared, versioned typography evidence. It never grants permission to erase pixels."""
from __future__ import annotations

import hashlib
import json
import math
from copy import deepcopy

import numpy as np
from app.core.timing import measured
from app.services.detection.text_angle import TEXT_ANGLE_VERSION, analyze_text_mask
from app.services.layout.text_frame import aligned_text_frame
from app.utils.geometry import polygon_to_bbox

ANALYSIS_KEY = "typography_analysis"
STATE_KEY = "typography_state"


def geometry_signature(region) -> str:
    return hashlib.sha256(json.dumps([region.bbox, region.polygon], separators=(",", ":")).encode()).hexdigest()


def oriented_foreground_frame(mask, origin, angle, padding, width, height):
    """Enclose every foreground pixel with bounded coordinate allocations."""
    sin, cos = math.sin(math.radians(angle)), math.cos(math.radians(angle))
    lower, upper = np.full(2, np.inf), np.full(2, -np.inf)
    for start in range(0, mask.shape[0], 128):
        rows, columns = np.nonzero(mask[start:start+128])
        if not len(rows):
            continue
        x, y = columns+origin[0]+.5, rows+start+origin[1]+.5
        u, v = x*cos+y*sin, -x*sin+y*cos
        lower = np.minimum(lower, [u.min(), v.min()])
        upper = np.maximum(upper, [u.max(), v.max()])
    if not np.isfinite(lower).all():
        return None
    radius = (abs(sin)+abs(cos))*.5
    left, top = lower-radius-[padding["left"], padding["top"]]
    right, bottom = upper+radius+[padding["right"], padding["bottom"]]
    local = np.asarray([[left, top], [right, top], [right, bottom], [left, bottom]])
    points = local@np.asarray([[cos, sin], [-sin, cos]])
    if (min(right-left, bottom-top) < 2 or np.any(points < 0)
            or np.any(points[:, 0] > width) or np.any(points[:, 1] > height)):
        return None  # Do not clip a rotated rectangle into another shape.
    return points.tolist()


def text_evidence(mask, origin, width, height, *, vertical, source, padding=None, fixed_direction=False):
    result = analyze_text_mask(mask, vertical, fixed_direction=fixed_direction)
    result["source"] = source
    if result["status"] == "accepted":
        result["target_polygon"] = oriented_foreground_frame(
            mask, origin, result["angle"], padding or dict.fromkeys(("left", "right", "top", "bottom"), 3.),
            width, height)
        if result["target_polygon"] is None:
            result.update(status="uncertain", reason="invalid_target_frame", angle=0.)
    return result


def automatic_target_is_current(region) -> bool:
    state = (region.layout_data or {}).get(STATE_KEY, {})
    return (state.get("applied") is True and state.get("target") == "auto" and state.get("target_polygon") == region.translated_polygon
            and state.get("target_bbox") == region.translated_bbox and state.get("angle") == region.rotation
            and (region.layout_data or {}).get(ANALYSIS_KEY, {}).get("source_geometry") == geometry_signature(region)
            and not region.perspective_warp)


def typography_patch(region, evidence, *, fresh=False) -> dict:
    """Pure patch; publication/version checks remain in the caller's transaction."""
    if not evidence:
        return {}
    layout = deepcopy(region.layout_data or {})
    previous = layout.get(STATE_KEY, {})
    analysis = {**deepcopy(evidence), "source_geometry": geometry_signature(region)}
    layout[ANALYSIS_KEY] = analysis
    patch = {"layout_data": layout}
    if evidence.get("source_geometry") not in {None, geometry_signature(region)}:
        layout[ANALYSIS_KEY].update(status="unavailable", reason="source_geometry_changed")
        return patch
    if region.locked or region.perspective_warp:
        return patch
    if evidence.get("version") != TEXT_ANGLE_VERSION:
        return patch
    # Untagged historical frames are deliberately not adopted retrospectively.
    if not fresh and not previous:
        return patch
    provenance = layout.get("text_rotation", {})
    pristine = (region.translated_polygon == region.polygon and region.translated_bbox == region.bbox
                and abs(region.rotation) <= .01)
    managed = (previous.get("target") == "auto" and previous.get("target_polygon") == region.translated_polygon
               and previous.get("target_bbox") == region.translated_bbox and previous.get("angle") == region.rotation)
    if (provenance.get("source") == "user" or previous.get("target") == "manual"
            or not (managed or pristine and (fresh or previous.get("target") == "auto"))):
        return patch
    state = {"version": 2, "orientation": "auto", **previous}
    if fresh:
        layout[STATE_KEY] = {**state, "target": "auto", "target_polygon": deepcopy(region.translated_polygon),
                             "target_bbox": deepcopy(region.translated_bbox), "angle": region.rotation,
                             "target_orientation": region.orientation}
    if evidence.get("status") not in {"accepted", "upright"}:
        return patch
    if evidence.get("orientation_confident") and state["orientation"] == "auto":
        patch["orientation"] = evidence["orientation"]
    angle = evidence["angle"]
    polygon = evidence.get("target_polygon")
    if angle and not polygon:
        return patch
    if angle:
        # A detector frame already aligned to the same angle supplies the same
        # local coordinates. Preserve it when it encloses the evidence frame.
        if fresh and aligned_text_frame(region.translated_polygon, angle):
            from app.services.detection.mask_geometry import points_inside
            points = np.asarray(polygon)
            if points_inside(points[:, 0], points[:, 1], np.asarray(region.translated_polygon)).all():
                polygon = deepcopy(region.translated_polygon)
        patch.update(translated_polygon=deepcopy(polygon), translated_bbox=polygon_to_bbox(polygon), rotation=angle)
    elif managed:
        # Upright-to-upright recognition still has to follow an edited source
        # frame before acknowledging the pending source/target synchronization.
        patch.update(translated_polygon=deepcopy(region.polygon), translated_bbox=deepcopy(region.bbox), rotation=0.)
    state.update(target="auto", applied=True, target_polygon=deepcopy(patch.get("translated_polygon", region.translated_polygon)),
                 target_bbox=deepcopy(patch.get("translated_bbox", region.translated_bbox)), angle=angle,
                 target_orientation=patch.get("orientation", region.orientation))
    layout[STATE_KEY] = state
    layout["text_rotation"] = {"version": 2, "source": evidence["source"], "angle": angle, "role": "text"}
    sync = layout.get("translation_geometry_sync", {})
    layout["translation_geometry_sync"] = {**sync, "version": 1, "pending": False,
        "revision": sync.get("revision", 0)+1, "direction": "automatic_typography",
        "source_bbox": deepcopy(region.bbox), "source_polygon": deepcopy(region.polygon),
        "previous_target_bbox": deepcopy(region.translated_bbox), "previous_target_polygon": deepcopy(region.translated_polygon),
        "previous_rotation": region.rotation, "previous_orientation": region.orientation}
    return patch


@measured("typography.manual")
def analyze_manual_regions(source, regions, settings, check):
    """One cached page segmentation and bounded local refinements per OCR pass."""
    import cv2
    from app.services.inpainting.local_text import refine_probability
    from app.services.inpainting.text_segmentation import text_probability
    from app.services.ocr.crops import _inside_mask

    selected = [row for row in regions if (row.layout_data or {}).get("manual")
                and (row.layout_data or {}).get(STATE_KEY) and not row.locked and not row.perspective_warp]
    if not selected:
        return {}
    cache = settings.data_dir / "cache" / "text-segmentation"
    check()
    note, probability = {"reason": "segmentation_disabled"}, None
    if settings.text_segmentation_enabled:
        try:
            probability, note = text_probability(source, settings.model_dir, cache, backend=settings.segmentation_backend)
            if probability is not None:
                probability, _ = refine_probability(source, settings.model_dir, probability,
                    [row.bbox for row in selected], cache, check=check, backend=settings.segmentation_backend)
        except (OSError, ValueError, cv2.error) as exc:
            note = {"reason": type(exc).__name__}
    results = {}
    for row in selected:
        check()
        if probability is None:
            results[row.id] = {"version": TEXT_ANGLE_VERSION, "source": "segmentation", "status": "unavailable",
                               "reason": note.get("reason", "segmentation_unavailable")}
            continue
        signature = geometry_signature(row)
        previous = (row.layout_data or {}).get(ANALYSIS_KEY, {})
        # Model/image cache identity is already in the probability cache; the
        # mask fingerprint below also rejects changed segmentation contents.
        height, width = probability.shape
        x, y, w, h = row.bbox
        left, top = max(0, math.floor(x)), max(0, math.floor(y))
        right, bottom = min(width, math.ceil(x+w)), min(height, math.ceil(y+h))
        if right <= left or bottom <= top:
            continue
        polygon = np.asarray(row.polygon, dtype=float)-[left, top]
        mask = (probability[top:bottom, left:right] >= .5) & _inside_mask(polygon, right-left, bottom-top)
        fixed = (row.layout_data or {}).get(STATE_KEY, {}).get("orientation") == "manual"
        result = text_evidence(mask, (left, top), width, height, vertical=row.orientation == "vertical",
                               fixed_direction=fixed, source="segmentation")
        result["source_geometry"] = signature
        results[row.id] = previous if previous == result else result
    return results

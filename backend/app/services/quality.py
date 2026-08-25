from __future__ import annotations

from app.core.config import get_settings
from app.models import ImagePage, ProcessingTask, TextRegion
from app.models.enums import TaskStatus
from app.schemas.domain import QualityIssue
from app.services.inpainting import mask_is_empty
from app.storage import get_storage
from app.utils.geometry import intersection_area


def evaluate_page(page: ImagePage, failed_tasks: list[ProcessingTask] | None = None) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    storage = get_storage()
    threshold = get_settings().ocr_review_threshold

    def add(code: str, message: str, region: TextRegion | None = None, severity: str = "warning") -> None:
        issues.append(
            QualityIssue(
                project_id=page.project_id,
                image_id=page.id,
                region_id=region.id if region else None,
                region_key=region.region_key if region else None,
                code=code,
                message=message,
                severity=severity,  # type: ignore[arg-type]
            )
        )

    for region in page.regions:
        reasons: list[str] = []
        if region.confidence < threshold and not region.locked:
            reasons.append("low_ocr_confidence")
            add("low_ocr_confidence", f"OCR confidence {region.confidence:.2f} is below {threshold:.2f}", region)
        if not region.source_text.strip():
            reasons.append("empty_source_text")
            add("empty_source_text", "OCR did not produce source text", region)
        if not region.translated_text.strip():
            reasons.append("missing_translation")
            add("missing_translation", "Region has no translated text", region)
        if (region.layout_data or {}).get("translation_fallback") and not region.locked:
            reasons.append("translation_fallback")
            add("translation_fallback", "Offline passthrough was used; translation requires review", region)
        if region.font_size < 10:
            reasons.append("font_too_small")
            add("font_too_small", "Rendered font size is below the readable minimum", region)
        if region.layout_warning or (region.layout_data or {}).get("overflow"):
            reasons.append("text_overflow")
            add("text_overflow", "Translated text does not fit the target region", region)
        x, y, width, height = region.bbox
        if x < 0 or y < 0 or x + width > page.width or y + height > page.height:
            reasons.append("image_boundary_collision")
            add("image_boundary_collision", "Text region intersects the image boundary", region, "error")
        if region.pixel_mask_path is None or mask_is_empty(storage.absolute(region.pixel_mask_path)):
            reasons.append("empty_mask")
            add("empty_mask", "Text mask is missing or empty", region)
        mask_generation = (region.layout_data or {}).get("mask_generation")
        constraint = mask_generation.get("constraint") if isinstance(mask_generation, dict) else None
        if isinstance(constraint, dict):
            constraint_status = constraint.get("status")
            if constraint_status in {"fallback", "skipped", "partial"}:
                reasons.append("repair_constraint_uncertain")
                add(
                    "repair_constraint_uncertain",
                    "Speech-balloon geometry could not safely constrain this repair mask",
                    region,
                )
            outside_value = constraint.get("outside_pixels_after", 0)
            if isinstance(outside_value, (int, float)) and outside_value > 0:
                reasons.append("repair_outside_balloon")
                add(
                    "repair_outside_balloon",
                    "Repair mask extends outside the protected speech-balloon interior",
                    region,
                    "error",
                )
        if not page.clean_path:
            reasons.append("inpainting_missing")
            add("inpainting_missing", "Inpainting has not been run", region)
        original_orientation = (region.layout_data or {}).get("source_orientation")
        if original_orientation and original_orientation != region.orientation:
            reasons.append("orientation_changed")
            add("orientation_changed", "Text direction changed after OCR", region)
        region.review_reasons = sorted(set(reasons))
        region.needs_review = bool(reasons)

    for index, first in enumerate(page.regions):
        for second in page.regions[index + 1 :]:
            area = intersection_area(first.bbox, second.bbox)
            if area > 0 and area / max(1.0, min(first.bbox[2] * first.bbox[3], second.bbox[2] * second.bbox[3])) > 0.12:
                add("region_overlap", f"Text overlaps {second.region_key}", first)
                if "region_overlap" not in first.review_reasons:
                    first.review_reasons = [*first.review_reasons, "region_overlap"]
                    first.needs_review = True
    for task in failed_tasks or []:
        if task.status == TaskStatus.FAILED.value:
            add("task_failed", task.error_message or "A processing task failed", severity="error")
    return issues

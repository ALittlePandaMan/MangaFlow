from __future__ import annotations

from app.core.config import get_settings
from app.models import ImagePage, ProcessingTask, TextRegion
from app.models.enums import TaskStatus
from app.schemas.domain import QualityIssue
from app.services.detection.text_roles import is_confirmed_free_text
from app.services.inpainting import mask_is_empty
from app.services.ocr.quality import ocr_review_hints
from app.storage import get_storage
from app.utils.geometry import intersection_area
from app.utils.text import has_text


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
        ocr_metadata = (region.layout_data or {}).get("ocr")
        has_confidence = not isinstance(ocr_metadata, dict) or ocr_metadata.get("confidence_available") is not False
        if has_confidence and region.confidence < threshold and not region.locked:
            reasons.append("low_ocr_confidence")
            add("low_ocr_confidence", f"OCR confidence {region.confidence:.2f} is below {threshold:.2f}", region)
        if not has_text(region.source_text):
            reasons.append("empty_source_text")
            add("empty_source_text", "OCR did not produce source text", region)
        if isinstance(ocr_metadata, dict) and ocr_metadata.get("generation_truncated"):
            reasons.append("ocr_generation_truncated")
            add("ocr_generation_truncated", "OCR 输出达到长度上限，请核对原图或提高输出上限", region)
        if isinstance(ocr_metadata, dict) and ocr_metadata.get("empty_chunks"):
            reasons.append("incomplete_ocr_chunks")
            add("incomplete_ocr_chunks", "长段落有部分行列未识别出文字，请核对原图", region)
        if (region.layout_data or {}).get("ocr_geometry_stale"):
            reasons.append("ocr_geometry_stale")
            add("ocr_geometry_stale", "原文框范围已修改，请按新框重新 OCR 并核对后再翻译", region)
        for hint in ocr_review_hints(region.source_text):
            reasons.append(hint)
            add(hint, "OCR 结果存在异常重复或无效字符，请核对原图", region)
        if not has_text(region.translated_text):
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
        integrity = (mask_generation.get("mask_integrity")
            if isinstance(mask_generation, dict) and mask_generation.get("method") != "manual_mask" else None)
        if isinstance(integrity, dict) and integrity.get("status") == "partial":
            reasons.append("incomplete_text_mask")
            add("incomplete_text_mask", "Text mask does not preserve all detected glyphs", region)
        elif isinstance(integrity, dict) and integrity.get("status") == "uncertain":
            reasons.append("mask_segmentation_disagreement")
            add("mask_segmentation_disagreement", "Local ink and text segmentation disagree; inspect the mask", region)
        constraint = mask_generation.get("constraint") if isinstance(mask_generation, dict) else None
        if isinstance(constraint, dict):
            constraint_status = constraint.get("status")
            # Older generators mislabeled RF-DETR's confirmed free text as a
            # skipped constraint. Correct that exact diagnostic without hiding
            # unknown roles, partial masks or actual geometry failures.
            known_free_text = (constraint_status == "skipped"
                               and constraint.get("reason") == "balloon_assignment_unassigned"
                               and is_confirmed_free_text((region.layout_data or {}).get("detection"), region.bubble_id))
            if constraint_status in {"fallback", "skipped", "partial"} and not known_free_text:
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
        repair_quality = (region.layout_data or {}).get("repair_quality", {})
        if isinstance(repair_quality, dict) and page.clean_path:
            if repair_quality.get("status") == "residual":
                reasons.append("residual_text")
                add("residual_text", "修复后仍检测到疑似文字残留，请检查该区域", region)
                if repair_quality.get("outside_mask_pixels", 0):
                    reasons.append("residual_outside_mask")
                    add("residual_outside_mask", "原蒙版附近疑似有漏擦文字；为保护原画未自动扩大擦除范围", region)
            elif repair_quality.get("status") == "uncertain":
                reasons.append("repair_verification_uncertain")
                add("repair_verification_uncertain", "整页与局部文字检查结果不一致，可能为纹理或文字残留，请确认净图", region)
            elif repair_quality.get("status") == "unavailable":
                reasons.append("repair_verification_unavailable")
                add("repair_verification_unavailable", "未能执行文字残留检查；净图需要人工确认", region)
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

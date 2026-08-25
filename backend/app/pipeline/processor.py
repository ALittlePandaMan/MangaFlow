from __future__ import annotations

import logging
import shutil
import time
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.secrets import get_secret_store
from app.models import ImagePage, ModelConfig, ProcessingTask, TextRegion, Translation
from app.models.enums import PageStatus, PipelineStage, TaskStatus
from app.services.base import DetectionResult
from app.services.infra.model_manifest import persist_model_settings
from app.services.infra.model_provisioning import ensure_recommended_config
from app.services.inpainting import create_text_mask, create_text_mask_union, load_text_mask_source
from app.services.quality import evaluate_page
from app.services.regions import save_revision
from app.services.registry import registry
from app.storage import get_storage
from app.utils.geometry import bbox_to_polygon, intersection_area, reading_order_japanese
from app.utils.image_metadata import load_rgb_with_metadata, save_png_with_metadata

logger = logging.getLogger(__name__)
STAGES = ["detection", "ocr", "translation", "mask", "inpainting", "rendering"]
PAGE_RUNNING_STATUS = {
    "detection": PageStatus.DETECTING.value,
    "ocr": PageStatus.OCR_RUNNING.value,
    "translation": PageStatus.TRANSLATING.value,
    "mask": PageStatus.MASK_GENERATING.value,
    "inpainting": PageStatus.INPAINTING.value,
    "rendering": PageStatus.RENDERING.value,
}
PAGE_DONE_STATUS = {
    "detection": PageStatus.DETECTED.value,
    "ocr": PageStatus.OCR_DONE.value,
    "translation": PageStatus.TRANSLATED.value,
    "mask": PageStatus.MASK_GENERATING.value,
    "inpainting": PageStatus.INPAINTED.value,
    "rendering": PageStatus.COMPLETED.value,
}


def _substantial_bbox_overlap(first: list[float], second: list[float], threshold: float = 0.65) -> bool:
    first_area = max(0.0, first[2]) * max(0.0, first[3])
    second_area = max(0.0, second[2]) * max(0.0, second[3])
    smaller_area = min(first_area, second_area)
    return smaller_area > 0 and intersection_area(first, second) / smaller_area >= threshold


def _exclude_preserved_geometry(
    result: DetectionResult,
    preserved: list[TextRegion],
) -> DetectionResult | None:
    if not preserved:
        return result
    grouping = result.metadata.get("line_grouping")
    source_boxes = grouping.get("source_boxes") if isinstance(grouping, dict) else None
    if not isinstance(source_boxes, list) or not source_boxes:
        return None if any(_substantial_bbox_overlap(result.bbox, region.bbox) for region in preserved) else result
    try:
        boxes = [[float(value) for value in box] for box in source_boxes if len(box) == 4]
    except (TypeError, ValueError):
        boxes = []
    if len(boxes) != len(source_boxes):
        return None if any(_substantial_bbox_overlap(result.bbox, region.bbox) for region in preserved) else result
    kept_indices = [
        index
        for index, box in enumerate(boxes)
        if not any(_substantial_bbox_overlap(box, region.bbox) for region in preserved)
    ]
    if len(kept_indices) == len(boxes):
        return result
    if not kept_indices:
        return None

    kept_boxes = [boxes[index] for index in kept_indices]
    left = min(box[0] for box in kept_boxes)
    top = min(box[1] for box in kept_boxes)
    right = max(box[0] + box[2] for box in kept_boxes)
    bottom = max(box[1] + box[3] for box in kept_boxes)
    bbox = [left, top, right - left, bottom - top]

    trimmed_grouping = deepcopy(grouping)
    trimmed_grouping["source_count"] = len(kept_indices)
    trimmed_grouping["source_boxes"] = kept_boxes
    source_polygons = grouping.get("source_polygons")
    trimmed_grouping["source_polygons"] = (
        [source_polygons[index] for index in kept_indices]
        if isinstance(source_polygons, list) and len(source_polygons) == len(boxes)
        else [bbox_to_polygon(box) for box in kept_boxes]
    )
    source_confidences = grouping.get("source_confidences")
    kept_confidences = (
        [float(source_confidences[index]) for index in kept_indices]
        if isinstance(source_confidences, list) and len(source_confidences) == len(boxes)
        else [result.confidence] * len(kept_indices)
    )
    trimmed_grouping["source_confidences"] = kept_confidences
    source_orientations = grouping.get("source_orientations")
    has_source_orientations = (
        isinstance(source_orientations, list)
        and len(source_orientations) == len(boxes)
        and all(isinstance(value, str) and value for value in source_orientations)
    )
    orientation = result.orientation
    if has_source_orientations:
        kept_orientations = [source_orientations[index] for index in kept_indices]
        trimmed_grouping["source_orientations"] = kept_orientations
        orientation_weights: dict[str, float] = {}
        for box, confidence, source_orientation in zip(
            kept_boxes,
            kept_confidences,
            kept_orientations,
            strict=True,
        ):
            weight = max(1.0, box[2] * box[3]) * max(0.01, confidence)
            orientation_weights[source_orientation] = orientation_weights.get(source_orientation, 0.0) + weight
        orientation = max(orientation_weights, key=orientation_weights.get)
    else:
        # Grouping metadata written before source orientations were recorded
        # remains valid; retain its aggregate direction as the safest fallback.
        trimmed_grouping.pop("source_orientations", None)
    index_map = {old_index: new_index for new_index, old_index in enumerate(kept_indices)}
    member_order = grouping.get("member_order")
    trimmed_order = (
        [index_map[index] for index in member_order if isinstance(index, int) and index in index_map]
        if isinstance(member_order, list)
        else []
    )
    trimmed_grouping["member_order"] = trimmed_order if len(trimmed_order) == len(kept_indices) else list(range(len(kept_indices)))
    metadata = deepcopy(result.metadata)
    metadata["line_grouping"] = trimmed_grouping
    return DetectionResult(
        polygon=bbox_to_polygon(bbox),
        bbox=bbox,
        confidence=min(kept_confidences),
        orientation=orientation,
        region_type=result.region_type,
        metadata=metadata,
        bubble_id=result.bubble_id,
    )


STAGE_PREVIOUS_STATUS = {
    "detection": PageStatus.UPLOADED.value,
    "ocr": PageStatus.DETECTED.value,
    "translation": PageStatus.OCR_DONE.value,
    "mask": PageStatus.TRANSLATED.value,
    "inpainting": PageStatus.MASK_GENERATING.value,
    "rendering": PageStatus.INPAINTED.value,
}


class TaskPaused(RuntimeError):
    pass


class TaskCancelled(RuntimeError):
    pass


class PipelineProcessor:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.storage = get_storage()
        self.settings = get_settings()
        self._active_task_id: str | None = None
        self._active_page: ImagePage | None = None
        self._page_status_before_stage: str | None = None
        self._page_error_before_stage: str | None = None
        self._last_progress_commit = 0.0

    async def execute(self, task_id: str) -> None:
        self._active_task_id = task_id
        task = self._task(task_id)
        page = self.db.scalar(
            select(ImagePage)
            .where(ImagePage.id == task.image_id)
            .options(selectinload(ImagePage.regions), selectinload(ImagePage.project))
        )
        if page is None:
            raise ValueError("Task page no longer exists")
        self._active_page = page
        self._page_status_before_stage = page.status
        self._page_error_before_stage = page.error_message
        payload = task.payload or {}
        start = str(payload.get("start_stage") or task.task_type or "detection")
        end = str(payload.get("end_stage") or task.task_type or "rendering")
        if start == PipelineStage.FULL.value:
            start = "detection"
        if end == PipelineStage.FULL.value:
            end = "rendering"
        if start not in STAGES or end not in STAGES or STAGES.index(start) > STAGES.index(end):
            raise ValueError(f"Invalid pipeline range: {start} -> {end}")
        stages = STAGES[STAGES.index(start) : STAGES.index(end) + 1]
        force = bool(payload.get("force", False))
        region_id = task.region_id
        options = payload.get("options", {})
        raw_region_ids = options.get("region_ids", []) if isinstance(options, dict) else []
        region_ids = {str(value) for value in raw_region_ids} if isinstance(raw_region_ids, list) and raw_region_ids else None
        for index, stage in enumerate(stages):
            self.db.refresh(task)
            self._page_status_before_stage = page.status
            self._page_error_before_stage = page.error_message
            if task.status in {TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}:
                self._finish_interrupted_page(page)
                raise TaskCancelled("Task cancelled")
            if task.pause_requested:
                task.status = TaskStatus.PAUSED.value
                task.message = f"Paused before {stage}"
                self._finish_interrupted_page(page)
                raise TaskPaused(task.message)
            task.current_stage = stage
            task.message = f"Running {stage}"
            page.current_stage = stage
            page.status = PAGE_RUNNING_STATUS[stage]
            page.error_message = None
            if stage in {"detection", "ocr"} and bool((page.metadata_json or {}).get("ocr_exempt", False)):
                metadata = dict(page.metadata_json or {})
                metadata.pop("ocr_exempt", None)
                page.metadata_json = metadata
            self.db.commit()
            if stage == "detection":
                self.detect(page, payload.get("provider"), force=force)
            elif stage == "ocr":
                self.ocr(
                    page,
                    payload.get("provider"),
                    force=force,
                    region_id=region_id,
                    region_ids=region_ids,
                    options=options,
                    progress=lambda completed, total, message, stage_index=index, stage_count=len(stages): self._commit_stage_progress(
                        task,
                        stage_index=stage_index,
                        stage_count=stage_count,
                        completed=completed,
                        total=total,
                        message=message,
                    ),
                )
            elif stage == "translation":
                await self.translate(
                    page, payload.get("provider"), force=force, region_id=region_id, region_ids=region_ids, options=options
                )
            elif stage == "mask":
                self.generate_masks(page, force=force, region_id=region_id, region_ids=region_ids, options=options)
            elif stage == "inpainting":
                self.inpaint(
                    page,
                    payload.get("provider"),
                    region_id=region_id,
                    region_ids=region_ids,
                    rebuild_clean=bool(options.get("rebuild_clean", False)),
                )
            elif stage == "rendering":
                self.render(page, payload.get("provider"), region_id=region_id)
            self.db.refresh(task)
            if task.status in {TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}:
                self._finish_interrupted_page(page)
                raise TaskCancelled("Task cancelled")
            page.status = PAGE_DONE_STATUS[stage]
            page.current_stage = None
            task.progress = (index + 1) / len(stages)
            self.db.commit()
            # Close the race where cancellation is committed after the check
            # above but before the stage-done page state is committed.
            self.db.refresh(task)
            if task.status in {TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}:
                self._finish_interrupted_page(page)
                raise TaskCancelled("Task cancelled")
        failed_tasks = list(
            self.db.scalars(
                select(ProcessingTask).where(
                    ProcessingTask.image_id == page.id,
                    ProcessingTask.status == TaskStatus.FAILED.value,
                    ProcessingTask.id != task.id,
                )
            ).all()
        )
        issues = evaluate_page(page, failed_tasks)
        if end == "rendering":
            page.status = PageStatus.NEEDS_REVIEW.value if issues else PageStatus.COMPLETED.value
        page.current_stage = None
        self.db.commit()

    def detect(self, page: ImagePage, provider_name: str | None, *, force: bool) -> None:
        existing = list(page.regions)
        if existing and not force:
            return
        self._invalidate_outputs(page, clean=True)
        preserved: list[TextRegion] = []
        for region in existing:
            layout_data = region.layout_data or {}
            # API-created regions were historically stored without provenance.
            # Treat those legacy records as manual too. Detector output always
            # carries the `detection` key, even when its metadata is empty.
            is_manual = bool(layout_data.get("manual")) or "detection" not in layout_data
            if region.locked or is_manual:
                preserved.append(region)
            else:
                self.db.delete(region)
        self.db.flush()
        provider, _ = self._provider("detection", provider_name)
        source = self.storage.absolute(page.original_path)
        detected = provider.detect(source)
        raw = [{"bbox": result.bbox} for result in detected]
        order = reading_order_japanese(raw)
        rank = {source_index: position + 1 for position, source_index in enumerate(order)}
        used_keys = {region.region_key for region in preserved}
        next_number = 1
        for index, result in enumerate(detected):
            # Manual and locked regions are authoritative. Forced re-detection
            # excludes only overlapping source members from a grouped result,
            # retaining any other text lines from that same bubble.
            result = _exclude_preserved_geometry(result, preserved)
            if result is None:
                continue
            while f"R{next_number:03d}" in used_keys:
                next_number += 1
            key = f"R{next_number:03d}"
            used_keys.add(key)
            next_number += 1
            self.db.add(
                TextRegion(
                    image_id=page.id,
                    region_key=key,
                    polygon=result.polygon,
                    bbox=result.bbox,
                    translated_polygon=result.polygon,
                    translated_bbox=result.bbox,
                    confidence=result.confidence,
                    orientation=result.orientation,
                    reading_order=rank.get(index, index + 1),
                    bubble_id=result.bubble_id,
                    # Keep detector boxes independent and use complex repair
                    # for every region instead of classifying flat bubbles.
                    region_type="background_complex",
                    layout_data={"source_orientation": result.orientation, "detection": result.metadata},
                )
            )
        self.db.flush()
        self.db.expire(page, ["regions"])
        list(page.regions)

    def ocr(
        self,
        page: ImagePage,
        provider_name: str | None,
        *,
        force: bool,
        region_id: str | None,
        options: dict[str, Any],
        region_ids: set[str] | None = None,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> None:
        self._invalidate_outputs(page, clean=False)
        provider, _ = self._provider("ocr", provider_name)
        source = self.storage.absolute(page.original_path)
        pending_regions = []
        for region in self._regions(page, region_id, region_ids):
            explicitly_forced = bool((region_id or region_ids) and force)
            if (region.locked and not explicitly_forced) or (region.source_text.strip() and not force):
                continue
            pending_regions.append(region)
        total = len(pending_regions)
        if total == 0:
            if progress:
                progress(0, 0, "OCR 0/0")
            return
        if progress:
            progress(0, total, f"Loading OCR model · 0/{total}")
        provider.ensure_loaded()
        if progress:
            progress(0, total, f"OCR 0/{total}")
        for completed, region in enumerate(pending_regions, start=1):
            orientation = str(options.get("orientation") or region.orientation)
            result = provider.recognize(source, region.bbox, orientation, int(options.get("crop_padding", 4)))
            save_revision(self.db, region, "ocr")
            region.source_text = result.text
            region.confidence = result.confidence
            region.orientation = result.orientation
            region.layout_data = {
                **(region.layout_data or {}),
                "source_orientation": result.orientation,
                "ocr": result.metadata,
            }
            reasons = [
                reason
                for reason in (region.review_reasons or [])
                if reason not in {"low_ocr_confidence", "empty_source_text"}
            ]
            if not result.text.strip():
                reasons.append("empty_source_text")
            if result.confidence < self.settings.ocr_review_threshold:
                reasons.append("low_ocr_confidence")
            region.review_reasons = sorted(set(reasons))
            region.needs_review = bool(reasons)
            if progress:
                progress(completed, total, f"OCR {completed}/{total}")

    def _commit_stage_progress(
        self,
        task: ProcessingTask,
        *,
        stage_index: int,
        stage_count: int,
        completed: int,
        total: int,
        message: str,
    ) -> None:
        now = time.monotonic()
        self.db.refresh(task)
        if task.status in {TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}:
            if self._active_page is not None:
                self._finish_interrupted_page(self._active_page)
            raise TaskCancelled("Task cancelled")
        if task.pause_requested:
            task.status = TaskStatus.PAUSED.value
            task.message = f"Paused during {task.current_stage or 'processing'}"
            if self._active_page is not None:
                self._finish_interrupted_page(self._active_page)
            raise TaskPaused(task.message)
        # Persist the first visible result and the terminal result immediately,
        # while batching fast middle-region updates to avoid an fsync per box.
        if completed not in {0, 1, total} and now - self._last_progress_commit < 0.5:
            return
        fraction = completed / total if total else 1.0
        task.progress = min(1.0, (stage_index + fraction) / max(1, stage_count))
        task.message = message
        self.db.commit()
        self._last_progress_commit = now

    def _finish_interrupted_page(self, page: ImagePage) -> None:
        page.current_stage = None
        page.status = self._page_status_before_stage or PageStatus.UPLOADED.value
        page.error_message = self._page_error_before_stage
        self.db.commit()

    def finish_interrupted_page(self) -> None:
        if self._active_page is not None:
            self._finish_interrupted_page(self._active_page)

    @staticmethod
    def stable_status_before(stage: str | None, current_status: str) -> str:
        """Best-effort recovery when the process lost its stage snapshot."""

        if stage and current_status == PAGE_RUNNING_STATUS.get(stage):
            return STAGE_PREVIOUS_STATUS[stage]
        return current_status

    async def translate(
        self,
        page: ImagePage,
        provider_name: str | None,
        *,
        force: bool,
        region_id: str | None,
        options: dict[str, Any],
        region_ids: set[str] | None = None,
    ) -> None:
        self._invalidate_outputs(page, clean=False)
        provider, resolved_name = self._provider("translation", provider_name)
        selected = [
            region
            for region in self._regions(page, region_id, region_ids)
            if (not region.locked or bool((region_id or region_ids) and force))
            and region.source_text.strip()
            and (force or not region.translated_text.strip())
        ]
        inputs = [(region.region_key, region.source_text) for region in selected]
        context = {
            **(page.project.translation_context or {}),
            "style": options.get("style") or provider.config.get("style", "natural manga dialogue"),
            "glossary": options.get("glossary") or provider.config.get("glossary", {}),
            "honorific_rules": options.get("honorific_rules")
            or provider.config.get("honorific_rules", "preserve when meaningful"),
            "onomatopoeia_strategy": options.get("onomatopoeia_strategy")
            or provider.config.get("onomatopoeia_strategy", "translate with concise equivalent"),
            "page_context": [
                {"id": region.region_key, "source": region.source_text, "translation": region.translated_text}
                for region in sorted(page.regions, key=lambda item: item.reading_order)
            ],
        }
        translations = await provider.translate_regions(
            inputs,
            source_language=page.project.source_language,
            target_language=page.project.target_language,
            context=context,
        )
        expected = {key for key, _ in inputs}
        if set(translations) != expected:
            raise ValueError(f"Translation ID mismatch: expected {sorted(expected)}, got {sorted(translations)}")
        for region in selected:
            save_revision(self.db, region, "translation")
            for previous in region.translations:
                previous.is_current = False
            region.translated_text = translations[region.region_key]
            fallback = resolved_name == "passthrough"
            region.layout_data = {**(region.layout_data or {}), "translation_fallback": fallback}
            self.db.add(
                Translation(
                    region_id=region.id,
                    source_text=region.source_text,
                    translated_text=region.translated_text,
                    source_language=page.project.source_language,
                    target_language=page.project.target_language,
                    provider=resolved_name,
                    model_name=str(provider.config.get("model", resolved_name)),
                )
            )
        self.db.flush()

    def generate_masks(
        self,
        page: ImagePage,
        *,
        force: bool,
        region_id: str | None,
        options: dict[str, Any],
        region_ids: set[str] | None = None,
    ) -> None:
        # A selected-region repair reuses the current clean image for every
        # unselected mask. Keep that page-level result available until the
        # inpainting stage has built the incremental replacement.
        partial_repair = bool(region_id or region_ids) and not bool(options.get("rebuild_clean", False))
        self._invalidate_outputs(page, clean=not partial_repair)
        page_directory = self.storage.page_dir(page.project_id, page.id)
        source = self.storage.absolute(page.original_path)
        regions = [
            region
            for region in self._regions(page, region_id, region_ids)
            if not (region.pixel_mask_path and not force)
        ]
        if not regions:
            return
        source_image, source_lab = load_text_mask_source(source)
        for region in regions:
            self._check_interruption()
            output = page_directory / "masks" / f"{region.id}.png"
            detection = (region.layout_data or {}).get("detection", {})
            line_grouping = detection.get("line_grouping", {}) if isinstance(detection, dict) else {}
            source_polygons = (
                line_grouping.get("source_polygons", [])
                if isinstance(line_grouping, dict)
                and line_grouping.get("method") == "balloon_instance"
                else []
            )
            valid_source_polygons = [
                polygon
                for polygon in source_polygons
                if isinstance(polygon, list)
                and len(polygon) >= 3
                and all(
                    isinstance(point, list)
                    and len(point) >= 2
                    and all(
                        isinstance(coordinate, (int, float)) and np.isfinite(coordinate)
                        for coordinate in point[:2]
                    )
                    for point in polygon
                )
            ]
            mask_arguments = {
                "expand": int(options.get("expand", 2)),
                "color_threshold": float(options.get("color_threshold", 18.0)),
                "source_image": source_image,
                "source_lab": source_lab,
            }
            if valid_source_polygons:
                metadata = create_text_mask_union(
                    source,
                    valid_source_polygons,
                    output,
                    **mask_arguments,
                )
            else:
                metadata = create_text_mask(
                    source,
                    region.polygon,
                    output,
                    **mask_arguments,
                )
            save_revision(self.db, region, "mask_generated")
            region.pixel_mask_path = self.storage.relative(output)
            region.region_type = "background_complex"
            region.layout_data = {**(region.layout_data or {}), "mask_generation": metadata}
        self.db.flush()

    def inpaint(
        self,
        page: ImagePage,
        provider_name: str | None,
        *,
        region_id: str | None,
        region_ids: set[str] | None = None,
        rebuild_clean: bool = False,
    ) -> None:
        self._invalidate_outputs(page, clean=False)
        provider, _ = self._provider("inpainting", provider_name)
        selected_regions = self._regions(page, region_id, region_ids)
        selected_ids = {region.id for region in selected_regions}
        page_directory = self.storage.page_dir(page.project_id, page.id)
        if (region_id or region_ids) and not rebuild_clean:
            self._inpaint_selected(page, provider, selected_regions, page_directory)
            return

        current = self.storage.absolute(page.original_path)
        final = page_directory / "clean" / "clean.png"
        regions = [
            region
            for region in sorted(page.regions, key=lambda item: item.reading_order)
            if region.pixel_mask_path
        ]
        # Rebuild from the immutable original using every saved mask, avoiding cumulative artifacts.
        # All masks are repaired together so unchanged pixels remain sourced
        # from the immutable original, regardless of the selected provider.
        if regions:
            combined_path = page_directory / "versions" / "combined-complex-mask.png"
            combined = None
            for region in regions:
                region.region_type = "background_complex"
                mask = cv2.imread(str(self.storage.absolute(region.pixel_mask_path)), cv2.IMREAD_GRAYSCALE)
                if mask is None:
                    raise ValueError(f"Cannot read mask for region {region.region_key}")
                if combined is not None and mask.shape != combined.shape:
                    raise ValueError("All page masks must have matching dimensions")
                combined = mask if combined is None else cv2.bitwise_or(combined, mask)
            combined_path.parent.mkdir(parents=True, exist_ok=True)
            if combined is None or not cv2.imwrite(str(combined_path), combined):
                raise ValueError("Cannot write combined complex mask")
            intermediate = page_directory / "versions" / "inpaint-complex.png"
            provider.inpaint(current, combined_path, intermediate, "background_complex")
            current = intermediate
            for region in regions:
                if region.id in selected_ids:
                    save_revision(self.db, region, "inpainted")
                    region.inpainted_path = self.storage.relative(intermediate)

        shutil.copy2(current, final)
        page.clean_path = self.storage.relative(final)
        self._record_artifact_version(page, "clean", final)
        self.db.flush()

    def _inpaint_selected(
        self,
        page: ImagePage,
        provider: Any,
        selected_regions: list[TextRegion],
        page_directory: Path,
    ) -> None:
        """Replace only selected repairs while retaining cached unselected pixels.

        The incremental source is rebuilt from the immutable original. Pixels
        from the existing clean image are copied back only through unselected
        masks, so an old selected mask that shrank or moved cannot leave a
        repaired ghost behind. The provider then receives only the union of the
        newly selected masks and therefore never reruns inference for other
        regions.
        """

        original_path = self.storage.absolute(page.original_path)
        original, metadata = load_rgb_with_metadata(original_path)
        selected_ids = {region.id for region in selected_regions}
        selected_mask = self._combined_region_mask(selected_regions)
        unselected_mask = self._combined_region_mask(
            [region for region in page.regions if region.id not in selected_ids and region.pixel_mask_path]
        )
        expected_shape = (original.height, original.width)
        if selected_mask is not None and selected_mask.shape != expected_shape:
            raise ValueError("Selected masks must match the original page dimensions")
        if unselected_mask is not None and unselected_mask.shape != expected_shape:
            raise ValueError("Unselected masks must match the original page dimensions")

        source = original
        if page.clean_path and unselected_mask is not None:
            clean_path = self.storage.absolute(page.clean_path)
            if clean_path.exists():
                clean, _ = load_rgb_with_metadata(clean_path)
                if clean.size != original.size:
                    raise ValueError("Clean image dimensions must match the original page")
                preserve = np.where(unselected_mask > 8, 255, 0).astype(np.uint8)
                source = Image.composite(clean, original, Image.fromarray(preserve))

        suffix = self._active_task_id or "manual"
        source_path = page_directory / "versions" / f"selected-inpaint-source-{suffix}.png"
        mask_path = page_directory / "versions" / f"selected-inpaint-mask-{suffix}.png"
        intermediate = page_directory / "versions" / f"inpaint-selected-{suffix}.png"
        final = page_directory / "clean" / "clean.png"
        save_png_with_metadata(source, source_path, metadata)

        current = source_path
        if selected_mask is not None:
            if not cv2.imwrite(str(mask_path), selected_mask):
                raise ValueError("Cannot write selected inpainting mask")
            provider.inpaint(source_path, mask_path, intermediate, "background_complex")
            current = intermediate
            for region in selected_regions:
                if not region.pixel_mask_path:
                    continue
                region.region_type = "background_complex"
                save_revision(self.db, region, "inpainted")
                region.inpainted_path = self.storage.relative(intermediate)

        shutil.copy2(current, final)
        page.clean_path = self.storage.relative(final)
        self._record_artifact_version(page, "clean", final)
        self.db.flush()
        source_path.unlink(missing_ok=True)
        mask_path.unlink(missing_ok=True)

    def _combined_region_mask(self, regions: list[TextRegion]) -> np.ndarray | None:
        combined = None
        for region in regions:
            if not region.pixel_mask_path:
                continue
            mask = cv2.imread(str(self.storage.absolute(region.pixel_mask_path)), cv2.IMREAD_GRAYSCALE)
            if mask is None:
                raise ValueError(f"Cannot read mask for region {region.region_key}")
            if combined is not None and mask.shape != combined.shape:
                raise ValueError("All page masks must have matching dimensions")
            combined = mask if combined is None else cv2.bitwise_or(combined, mask)
        return combined

    def render(self, page: ImagePage, provider_name: str | None, *, region_id: str | None) -> None:
        provider, _ = self._provider("rendering", provider_name)
        background = self.storage.absolute(page.clean_path or page.original_path)
        page_directory = self.storage.page_dir(page.project_id, page.id)
        output = page_directory / "rendered" / "translated.png"
        text_layer = page_directory / "layers" / "translated-text.png"
        result = provider.render(background, page.regions, output, text_layer)
        for region in page.regions:
            if region.id in result["layouts"]:
                layout = result["layouts"][region.id]
                save_revision(self.db, region, "rendered")
                region.font_size = float(layout["font_size"])
                region.layout_warning = bool(layout["overflow"])
                region.layout_data = {**(region.layout_data or {}), **layout}
        page.rendered_path = self.storage.relative(output)
        page.text_layer_path = self.storage.relative(text_layer)
        self._record_artifact_version(page, "rendered", output)
        self._record_artifact_version(page, "text_layer", text_layer)
        self.db.flush()

    def _provider(self, kind: str, requested: str | None) -> tuple[Any, str]:
        query = select(ModelConfig).where(ModelConfig.kind == kind, ModelConfig.enabled.is_(True))
        if requested:
            query = query.where((ModelConfig.provider == requested) | (ModelConfig.name == requested))
        else:
            query = query.where(ModelConfig.is_default.is_(True))
        configured = self.db.scalar(query.order_by(ModelConfig.updated_at.desc()))
        if configured is None and not requested and self.settings.auto_provision_models:
            configured, _ = ensure_recommended_config(self.db, kind)
            self.db.commit()
            persist_model_settings(
                self.db,
                self.settings.model_manifest_path,
                environment_path=self.settings.environment_file_path,
            )
        if configured:
            config = dict(configured.config or {})
            secret = get_secret_store().decrypt(configured.encrypted_api_key)
            if secret:
                config["api_key"] = secret
            return registry.create(kind, configured.provider, config), configured.provider
        resolved = requested or registry.defaults[kind]
        return registry.create(kind, resolved), resolved

    @staticmethod
    def _regions(
        page: ImagePage,
        region_id: str | None,
        region_ids: set[str] | None = None,
    ) -> list[TextRegion]:
        if not region_id and not region_ids:
            return list(page.regions)
        requested = {region_id} if region_id else set(region_ids or ())
        selected = [region for region in page.regions if region.id in requested]
        if {region.id for region in selected} != requested:
            raise ValueError("One or more selected regions do not belong to the task page")
        return selected

    def _task(self, task_id: str) -> ProcessingTask:
        task = self.db.get(ProcessingTask, task_id)
        if task is None:
            raise ValueError("Task no longer exists")
        return task

    @staticmethod
    def _invalidate_outputs(page: ImagePage, *, clean: bool) -> None:
        metadata = dict(page.metadata_json or {})
        versions = dict(metadata.get("artifact_versions", {}))
        if clean:
            page.clean_path = None
            versions.pop("clean", None)
        page.rendered_path = None
        page.text_layer_path = None
        versions.pop("rendered", None)
        versions.pop("text_layer", None)
        if versions:
            metadata["artifact_versions"] = versions
        else:
            metadata.pop("artifact_versions", None)
        page.metadata_json = metadata

    @staticmethod
    def _record_artifact_version(page: ImagePage, artifact: str, path: Path) -> None:
        metadata = dict(page.metadata_json or {})
        versions = dict(metadata.get("artifact_versions", {}))
        versions[artifact] = path.stat().st_mtime_ns
        page.metadata_json = {**metadata, "artifact_versions": versions}

    def _check_interruption(self) -> None:
        if not self._active_task_id:
            return
        task = self._task(self._active_task_id)
        self.db.refresh(task)
        if task.status in {TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}:
            if self._active_page is not None:
                self._finish_interrupted_page(self._active_page)
            raise TaskCancelled("Task cancelled")
        if task.pause_requested:
            task.status = TaskStatus.PAUSED.value
            task.message = f"Paused during {task.current_stage or 'processing'}"
            if self._active_page is not None:
                self._finish_interrupted_page(self._active_page)
            raise TaskPaused(task.message)

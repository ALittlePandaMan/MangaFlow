"""Frozen batch context and non-blocking, translation-only page dependencies."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.transactions import task_write
from app.models import (
    ImagePage,
    ModelConfig,
    ProcessingBatch,
    ProcessingTask,
    StageCheckpoint,
    TaskWorkflow,
    TextRegion,
)
from app.services.translation.context import page_context
from app.services.workflow_state import fingerprint, record
from sqlalchemy import select
from sqlalchemy.orm import load_only

POLICY_SETTINGS = ("text_segmentation_enabled", "repair_auto_retry", "ocr_review_threshold", "segmentation_backend")
PROJECT_POLICY = ("source_language", "target_language", "translation_context", "settings")


def prepare_batch(db, project, pages, payload) -> ProcessingBatch | None:
    if not get_settings().checkpoint_pipeline_enabled or not pages:
        return None
    if (payload.get("start_stage") not in {"full", "detection", "ocr", "translation"}
            or payload.get("end_stage") not in {"full", "rendering"} or payload.get("options", {}).get("region_ids")):
        return None
    from app.pipeline.processor import PipelineProcessor

    # Provision before adding any batch/tasks; this may persist model settings.
    processor = PipelineProcessor(db)
    kinds = ("detection", "ocr", "translation", "inpainting", "rendering") if payload.get("start_stage") in {"full", "detection"} else ("ocr", "translation", "inpainting", "rendering")
    for kind in kinds:
        processor._provider(kind, None)
    mode = (project.settings or {}).get("translation_context_mode", "snapshot")
    if mode not in {"snapshot", "previous_page"}:
        raise ValueError("Unsupported project translation context mode")
    ordered = list(db.execute(select(ImagePage.id, ImagePage.order_index).where(
        ImagePage.project_id == project.id,
    ).order_by(ImagePage.order_index, ImagePage.id)))
    predecessors = {row.id: ordered[index - 1].id if index else None for index, row in enumerate(ordered)}
    prior_ids = {predecessors[page.id] for page in pages} - {None}
    groups = {identifier: [] for identifier in prior_ids}
    # No masks, history, layout blobs or images are needed for scene context.
    for region in db.scalars(select(TextRegion).where(TextRegion.image_id.in_(prior_ids)).options(load_only(
        TextRegion.image_id, TextRegion.region_key, TextRegion.source_text, TextRegion.translated_text,
        TextRegion.reading_order, TextRegion.panel_id, TextRegion.bubble_id, TextRegion.bbox, TextRegion.orientation,
    )).order_by(TextRegion.reading_order, TextRegion.created_at, TextRegion.id)):
        groups[region.image_id].append(region)
    batch = ProcessingBatch(project_id=project.id, context_mode=mode,
        policy={"project": {key: deepcopy(getattr(project, key)) for key in PROJECT_POLICY},
                "settings": {key: getattr(get_settings(), key) for key in POLICY_SETTINGS},
                "models": [record(row) for row in db.scalars(select(ModelConfig).where(ModelConfig.enabled.is_(True)).order_by(ModelConfig.id))]},
        contexts={page.id: ({"page_id": previous, "regions": page_context(groups[previous], character_budget=4000)}
                            if (previous := predecessors[page.id]) else None) for page in pages})
    db.add(batch)
    db.flush()
    return batch


def dependency_admission(task_id: str) -> str:
    """One short DB operation, never wait with a connection or resource permit."""
    with SessionLocal() as db:
        def examine():
            task = db.scalar(select(ProcessingTask).where(ProcessingTask.id == task_id).with_for_update())
            if task is None or task.status != "QUEUED" or task.pause_requested:
                return "stopped"
            if not task.dependency_task_id or task.dependency_context is not None:
                if task.waiting_reason:
                    task.waiting_reason = None
                return "ready"
            previous = db.get(ProcessingTask, task.dependency_task_id)
            if previous is None or previous.project_id != task.project_id or previous.batch_id != task.batch_id:
                return _failed(task, "前页翻译任务不存在；请重新创建批次", "dependency_missing")
            # Depend on the translation checkpoint, never the previous page's
            # repair/render completion. A failed repair can still supply text.
            result = db.scalar(select(StageCheckpoint.result).join(TaskWorkflow).where(
                TaskWorkflow.task_id == previous.id, TaskWorkflow.generation == previous.execution_generation,
                TaskWorkflow.status.in_(["RUNNING", "PUBLISHED"]), StageCheckpoint.stage == "translation",
                StageCheckpoint.status == "SUCCEEDED",
            ).order_by(StageCheckpoint.created_at.desc()).limit(1))
            if result is not None and isinstance(result.get("context"), dict):
                task.dependency_context = deepcopy(result["context"])
                task.waiting_reason, task.message = None, "前页翻译已就绪，等待处理名额"
                return "ready"
            if previous.status in {"FAILED", "CANCELLED", "COMPLETED"}:
                return _failed(task, "前页没有可用翻译结果；请先重试前页，再重试此页", "dependency_failed")
            reason = "previous_page_paused" if previous.status == "PAUSED" else "previous_page_translation"
            if task.waiting_reason != reason:
                task.waiting_reason = reason
                task.message = "等待前页翻译（未占用处理名额）"
            source_repair = (task.payload or {}).get("options",{}).get("repair_geometry","source") == "source"
            if source_repair and get_settings().parallel_repair_translation:
                prepared = db.scalar(select(StageCheckpoint.id).join(TaskWorkflow).where(
                    TaskWorkflow.task_id == task.id,TaskWorkflow.generation == task.execution_generation,
                    TaskWorkflow.status == "RUNNING",StageCheckpoint.stage == "inpainting",StageCheckpoint.status == "SUCCEEDED",
                ).limit(1))
                if prepared is None:
                    return "repair"
            return "waiting"
        return task_write(db, task_id, examine)


def _failed(task, message, kind):
    task.status, task.error_message, task.error_kind = "FAILED", message, kind
    task.waiting_reason, task.current_stage, task.active_stages = None, None, []
    task.message, task.finished_at = "前页依赖未满足", datetime.now(timezone.utc)
    return "failed"


def batch_policy(db, task):
    """Return private frozen values without exposing them in task API payloads."""
    if not task.batch_id:
        return None
    batch = db.get(ProcessingBatch, task.batch_id)
    if batch is None or batch.project_id != task.project_id:
        raise ValueError("Batch snapshot is unavailable")
    # A dependency is part of the immutable request, but its eventual contents
    # belong only to the translation-stage key. Native checkpoints must remain
    # reusable when the previous page's text arrives after repair completed.
    context = None if task.dependency_task_id else batch.contexts.get(task.image_id)
    return {**deepcopy(batch.policy), "previous_page": deepcopy(context),
            "batch": {"id": batch.id, "mode": batch.context_mode,"dependency_task_id":task.dependency_task_id,
                      "context_fingerprint": fingerprint(task.dependency_task_id or context)}}

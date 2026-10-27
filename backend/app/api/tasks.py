from __future__ import annotations

import asyncio
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.api.helpers import require_project, require_task
from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.models import ImagePage, ProcessingBatch, ProcessingTask, TaskExecutionRun, TextRegion
from app.models.enums import PageStatus, TaskStatus
from app.schemas.domain import BatchProcessRequest, TaskRead
from app.services.mcp_context import check_page_version, commit_page_tasks
from app.tasks import task_manager

router = APIRouter(tags=["tasks"])


def _task_read(task: ProcessingTask) -> TaskRead:
    result = TaskRead.model_validate(task)
    error = task_manager.terminal_error(task.id)
    if error and task.status in {TaskStatus.RUNNING.value, TaskStatus.CANCELLING.value}:
        # A worker is known to have exited. DB recovery may still be waiting for
        # a writer lock; do not mutate/flush the request's ORM entity to report it.
        return result.model_copy(update={
            "status": TaskStatus.FAILED.value,
            "current_stage": None,
            "message": "任务已停止，正在恢复保存状态",
            "error_message": error,
        })
    return result

OCR_COMPLETE_STATUSES = {
    PageStatus.OCR_DONE.value,
    PageStatus.TRANSLATING.value,
    PageStatus.TRANSLATED.value,
    PageStatus.MASK_GENERATING.value,
    PageStatus.INPAINTING.value,
    PageStatus.INPAINTED.value,
    PageStatus.LAYOUTING.value,
    PageStatus.RENDERING.value,
    PageStatus.COMPLETED.value,
    PageStatus.NEEDS_REVIEW.value,
}


@router.get("/tasks", response_model=list[TaskRead])
def list_tasks(
    project_id: str | None = None,
    image_id: str | None = None,
    status: str | None = None,
    active_first: bool = False,
    limit: int = Query(default=500, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[TaskRead]:
    query = select(ProcessingTask)
    if project_id:
        query = query.where(ProcessingTask.project_id == project_id)
    if image_id:
        query = query.where(ProcessingTask.image_id == image_id)
    if status:
        query = query.where(ProcessingTask.status == status.upper())
    if active_first:
        # Small canvas lists must not hide a running task behind newer history.
        query = query.order_by(case(
            (ProcessingTask.status.in_(["RUNNING", "CANCELLING"]), 0),
            (ProcessingTask.status == "QUEUED", 1),
            (ProcessingTask.status == "PAUSED", 2),
            else_=3,
        ))
    return [_task_read(task) for task in db.scalars(query.order_by(
        ProcessingTask.created_at.desc(), ProcessingTask.id.desc(),
    ).offset(offset).limit(limit)).all()]


@router.get("/tasks/{task_id}/timings")
def task_timings(
    task_id: str,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    require_task(db, task_id)
    runs = db.scalars(select(TaskExecutionRun).where(TaskExecutionRun.task_id == task_id).order_by(
        TaskExecutionRun.started_at.desc(), TaskExecutionRun.id.desc(),
    ).offset(offset).limit(limit)).all()
    return {
        "task_id": task_id,
        "timing_semantics": "inclusive_components_do_not_sum_as_wall_time",
        "runs": [{
            "id": run.id, "status": run.status, "started_at": run.started_at, "finished_at": run.finished_at,
            "queue_seconds": run.queue_seconds, "wall_seconds": run.wall_seconds, "timings": run.timings,
            "partial": run.status in {"RUNNING", "INTERRUPTED"},
        } for run in runs],
    }


@router.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(task_id: str, db: Session = Depends(get_db)) -> TaskRead:
    return _task_read(require_task(db, task_id))


@router.post("/tasks/status", response_model=list[TaskRead])
def task_statuses(task_ids: list[str], db: Session = Depends(get_db)) -> list[TaskRead]:
    """Read one bounded batch, including terminal rows beyond the list's 500-row window."""
    if not task_ids or len(task_ids) > 400:
        raise HTTPException(422, "请选择 1 到 400 个任务查询状态")
    tasks = {task.id: task for task in db.scalars(
        select(ProcessingTask).where(ProcessingTask.id.in_(set(task_ids)))
    )}
    if len(tasks) != len(set(task_ids)):
        raise HTTPException(404, "部分任务已不存在，请刷新任务列表")
    return [_task_read(tasks[identifier]) for identifier in dict.fromkeys(task_ids)]


@router.post("/tasks/{task_id}/pause", response_model=TaskRead)
def pause_task(task_id: str, db: Session = Depends(get_db)) -> ProcessingTask:
    task = require_task(db, task_id, for_update=True)
    if task.status not in {TaskStatus.QUEUED.value, TaskStatus.RUNNING.value}:
        raise HTTPException(409, f"Cannot pause a {task.status} task")
    task.pause_requested = True
    if task.status == TaskStatus.QUEUED.value:
        task.status = TaskStatus.PAUSED.value
        task.message = "Paused while queued"
    db.commit()
    db.refresh(task)
    if task.status == TaskStatus.PAUSED.value:
        task_manager.cancel(task.id)  # Stop only the queued wrapper, not work in flight.
    task_manager.notify_dependency(task.id)
    if task.dependency_task_id:
        task_manager.notify_dependency(task.dependency_task_id)
    return task


@router.post("/tasks/{task_id}/resume", response_model=TaskRead, status_code=202)
def resume_task(task_id: str, allow_unknown_cloud_retry: bool = Query(default=False), db: Session = Depends(get_db)) -> ProcessingTask:
    task = require_task(db, task_id)
    project_id, page_id = task.project_id, task.image_id

    def resume(versions):
        task = require_task(db, task_id, for_update=True)
        if task.status != TaskStatus.PAUSED.value:
            raise HTTPException(409, "Only paused tasks can be resumed")
        if task_manager.is_active(task_id):
            raise HTTPException(409, "Wait for the paused worker to stop before resuming")
        _confirm_unknown_requests(db, task_id, allow_unknown_cloud_retry)
        task.pause_requested = False
        task.status = TaskStatus.QUEUED.value
        task.error_kind, task.waiting_reason, task.active_stages = None, None, []
        task.message = "Queued to resume"
        if versions:
            task.payload = {**task.payload, "_mcp_admission_version": versions[page_id]}
        return task

    task = commit_page_tasks(db, project_id, [page_id] if page_id else [], resume)
    db.refresh(task)
    task_manager.dispatch(task.id)
    task_manager.promote(task.id)
    return task


@router.post("/tasks/{task_id}/retry", response_model=TaskRead, status_code=202)
def retry_task(task_id: str, allow_unknown_cloud_retry: bool = Query(default=False), db: Session = Depends(get_db)) -> ProcessingTask:
    task = require_task(db, task_id)
    project_id, page_id = task.project_id, task.image_id

    def retry(versions):
        task = require_task(db, task_id, for_update=True)
        if task.status not in {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
            raise HTTPException(409, "Only failed or cancelled tasks can be retried")
        if task_manager.is_active(task_id):
            raise HTTPException(409, "Wait for the previous worker to stop before retrying")
        _confirm_unknown_requests(db, task_id, allow_unknown_cloud_retry)
        from app.services.mcp_context import workflow_run_id
        if workflow_run_id.get():
            task.payload = {**task.payload, "options": {**task.payload.get("options", {}), "_workflow_previous_status": task.status}}
        task.status = TaskStatus.QUEUED.value
        task.progress = 0.0
        task.pause_requested = False
        task.error_message = None
        task.error_kind, task.waiting_reason, task.active_stages = None, None, []
        task.current_stage = None
        task.started_at = None
        task.finished_at = None
        task.message = "Queued for retry"
        if versions:
            task.payload = {**task.payload, "_mcp_admission_version": versions[page_id]}
        return task

    task = commit_page_tasks(db, project_id, [page_id] if page_id else [], retry)
    db.refresh(task)
    task_manager.dispatch(task.id)
    task_manager.promote(task.id)
    return task


def _confirm_unknown_requests(db: Session, task_id: str, allow: bool) -> None:
    from app.services.checkpoints import unknown_cloud_outcomes
    unknown = unknown_cloud_outcomes(db, task_id)
    if unknown and not allow:
        raise HTTPException(409, "上次云端请求结果不确定，重试可能重复计费；请明确确认后再重试")
    for checkpoint in unknown:
        checkpoint.status = "ACKNOWLEDGED"


@router.post("/tasks/{task_id}/cancel", response_model=TaskRead)
def cancel_task(task_id: str, db: Session = Depends(get_db)) -> ProcessingTask:
    task = require_task(db, task_id, for_update=True)
    if task.status in {TaskStatus.COMPLETED.value, TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}:
        raise HTTPException(409, f"Cannot cancel a {task.status} task")
    # Publish the cooperative state before touching the in-memory queue. A
    # worker that wins the start race will now observe CANCELLING, while the
    # manager atomically decides whether this dispatch had actually started.
    task.status = TaskStatus.CANCELLING.value
    task.message = "Stopping current work"
    db.commit()
    if not task_manager.cancel(task.id):
        task_manager._finish_cancelled_task(task)
        db.commit()
    db.refresh(task)
    task_manager.notify_dependency(task.id)
    return task


@router.post("/projects/{project_id}/batch-process", response_model=list[TaskRead], status_code=202)
def batch_process(project_id: str, payload: BatchProcessRequest, db: Session = Depends(get_db)) -> list[ProcessingTask]:
    project = require_project(db, project_id)
    query = select(ImagePage).where(ImagePage.project_id == project_id)
    if payload.only_unrecognized:
        query = query.options(selectinload(ImagePage.regions))
    if payload.image_ids is not None:
        query = query.where(ImagePage.id.in_(payload.image_ids))
    pages = list(db.scalars(query.order_by(ImagePage.order_index)).all())
    if payload.image_ids is not None and len(pages) != len(set(payload.image_ids)):
        raise HTTPException(404, "One or more selected pages were not found in this project")
    if payload.only_unrecognized or payload.only_with_regions:
        active_image_ids = set(
            db.scalars(
                select(ProcessingTask.image_id).where(
                    ProcessingTask.project_id == project_id,
                    ProcessingTask.status.in_([
                        TaskStatus.QUEUED.value,
                        TaskStatus.RUNNING.value,
                        TaskStatus.CANCELLING.value,
                        TaskStatus.PAUSED.value,
                    ]),
                )
            ).all()
        )
        pages = [page for page in pages if page.id not in active_image_ids]
    if payload.only_unrecognized:
        pages = [page for page in pages if _page_needs_ocr(page)]
    if payload.only_with_regions:
        # Eligibility needs IDs only, not every page's polygons, masks and text.
        with_regions = set(db.scalars(
            select(TextRegion.image_id).join(ImagePage, ImagePage.id == TextRegion.image_id)
            .where(ImagePage.project_id == project_id).distinct()
        ).all())
        pages = [page for page in pages if page.id in with_regions]
    task_payload = payload.model_dump(mode="json", exclude={"image_ids", "only_unrecognized", "only_with_regions"})
    task_payload["batch_submission"] = True
    from app.services.batch_context import prepare_batch
    for page in pages:
        check_page_version(db, page.id)
    batch = prepare_batch(db, project, pages, task_payload, persist=False)
    identifiers = {page.id: str(uuid4()) for page in pages}
    page_ids = list(identifiers)

    def create(versions):
        if batch:
            db.add(batch)
            db.flush()
        tasks = []
        for page_id in page_ids:
            previous = (batch.contexts.get(page_id) or {}).get("page_id") if batch else None
            task = ProcessingTask(id=identifiers[page_id], project_id=project_id, image_id=page_id,
                task_type="full", status=TaskStatus.QUEUED.value,
                payload={**task_payload, **({"_mcp_admission_version": versions[page_id]} if versions else {})},
                message="Queued by batch process", batch_id=batch.id if batch else None,
                dependency_task_id=identifiers.get(previous) if batch and batch.context_mode == "previous_page" else None)
            db.add(task)
            tasks.append(task)
        return tasks

    tasks = commit_page_tasks(db, project_id, page_ids, create)
    for task in tasks:
        db.refresh(task)
        task_manager.dispatch(task.id)
    return tasks


@router.get("/projects/{project_id}/batches/{batch_id}")
def batch_status(project_id: str, batch_id: str, db: Session = Depends(get_db)) -> dict:
    batch = db.get(ProcessingBatch, batch_id)
    if batch is None or batch.project_id != project_id:
        raise HTTPException(404, "Batch not found")
    counts = dict(db.execute(select(ProcessingTask.status, func.count()).where(
        ProcessingTask.batch_id == batch_id,
    ).group_by(ProcessingTask.status)).all())
    waiting = db.scalar(select(func.count()).select_from(ProcessingTask).where(
        ProcessingTask.batch_id == batch_id, ProcessingTask.status == "QUEUED", ProcessingTask.waiting_reason.is_not(None),
    ))
    total = len(batch.contexts)
    completed = counts.get("COMPLETED", 0)
    return {"id": batch_id, "project_id": project_id, "context_mode": batch.context_mode,
            "total": total, "completed": completed, "waiting": waiting, "statuses": counts,
            "missing": max(0, total - sum(counts.values())),
            "progress": completed / total if total else 0.0}


def _page_needs_ocr(page: ImagePage) -> bool:
    if bool((page.metadata_json or {}).get("ocr_exempt", False)):
        return False
    if page.status in OCR_COMPLETE_STATUSES:
        return False
    if page.status != PageStatus.FAILED.value:
        return True
    # A later-stage failure must not make a page with valid OCR eligible for
    # batch recognition again. A failed or partial OCR page remains eligible.
    return not page.regions or any(not region.source_text.strip() for region in page.regions)


@router.websocket("/ws/tasks/{task_id}")
async def task_updates(websocket: WebSocket, task_id: str) -> None:
    if get_settings().maintenance_mode:
        await websocket.close(code=1013)
        return
    await websocket.accept()
    try:
        last_version: tuple | None = None
        while True:
            with SessionLocal() as db:
                task = db.get(ProcessingTask, task_id)
                if task is None:
                    await websocket.send_json({"error": "Task not found"})
                    await websocket.close(code=4404)
                    return
                task = _task_read(task)
                version = (
                    task.status,
                    task.progress,
                    task.current_stage,
                    task.message,
                    task.error_message,
                    task.updated_at,
                )
                if version != last_version:
                    await websocket.send_json(
                        {
                            "id": task.id,
                            "status": task.status,
                            "progress": task.progress,
                            "current_stage": task.current_stage,
                            "message": task.message,
                            "error_message": task.error_message,
                        }
                    )
                    last_version = version
                terminal = task.status in {
                    TaskStatus.COMPLETED.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED.value,
                }
            if terminal:
                await websocket.close()
                return
            await asyncio.sleep(0.5)
    except WebSocketDisconnect:
        return

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.helpers import require_project, require_task
from app.core.database import SessionLocal, get_db
from app.models import ImagePage, ProcessingTask
from app.models.enums import PageStatus, TaskStatus
from app.schemas.domain import BatchProcessRequest, TaskRead
from app.tasks import task_manager

router = APIRouter(tags=["tasks"])

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
    db: Session = Depends(get_db),
) -> list[ProcessingTask]:
    query = select(ProcessingTask)
    if project_id:
        query = query.where(ProcessingTask.project_id == project_id)
    if image_id:
        query = query.where(ProcessingTask.image_id == image_id)
    if status:
        query = query.where(ProcessingTask.status == status.upper())
    return list(db.scalars(query.order_by(ProcessingTask.created_at.desc()).limit(500)).all())


@router.get("/tasks/{task_id}", response_model=TaskRead)
def get_task(task_id: str, db: Session = Depends(get_db)) -> ProcessingTask:
    return require_task(db, task_id)


@router.post("/tasks/{task_id}/pause", response_model=TaskRead)
def pause_task(task_id: str, db: Session = Depends(get_db)) -> ProcessingTask:
    task = require_task(db, task_id)
    if task.status not in {TaskStatus.QUEUED.value, TaskStatus.RUNNING.value}:
        raise HTTPException(409, f"Cannot pause a {task.status} task")
    task.pause_requested = True
    if task.status == TaskStatus.QUEUED.value:
        task.status = TaskStatus.PAUSED.value
        task.message = "Paused while queued"
    db.commit()
    db.refresh(task)
    return task


@router.post("/tasks/{task_id}/resume", response_model=TaskRead, status_code=202)
def resume_task(task_id: str, db: Session = Depends(get_db)) -> ProcessingTask:
    task = require_task(db, task_id)
    if task.status != TaskStatus.PAUSED.value:
        raise HTTPException(409, "Only paused tasks can be resumed")
    if task_manager.is_active(task_id):
        raise HTTPException(409, "Wait for the paused worker to stop before resuming")
    task.pause_requested = False
    task.status = TaskStatus.QUEUED.value
    task.message = "Queued to resume"
    db.commit()
    db.refresh(task)
    task_manager.dispatch(task.id)
    return task


@router.post("/tasks/{task_id}/retry", response_model=TaskRead, status_code=202)
def retry_task(task_id: str, db: Session = Depends(get_db)) -> ProcessingTask:
    task = require_task(db, task_id)
    if task.status not in {TaskStatus.FAILED.value, TaskStatus.CANCELLED.value}:
        raise HTTPException(409, "Only failed or cancelled tasks can be retried")
    if task_manager.is_active(task_id):
        raise HTTPException(409, "Wait for the previous worker to stop before retrying")
    task.status = TaskStatus.QUEUED.value
    task.progress = 0.0
    task.pause_requested = False
    task.error_message = None
    task.current_stage = None
    task.started_at = None
    task.finished_at = None
    task.message = "Queued for retry"
    db.commit()
    db.refresh(task)
    task_manager.dispatch(task.id)
    return task


@router.post("/tasks/{task_id}/cancel", response_model=TaskRead)
def cancel_task(task_id: str, db: Session = Depends(get_db)) -> ProcessingTask:
    task = require_task(db, task_id)
    if task.status in {TaskStatus.COMPLETED.value, TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}:
        raise HTTPException(409, f"Cannot cancel a {task.status} task")
    # Publish the cooperative state before touching the in-memory queue. A
    # worker that wins the start race will now observe CANCELLING, while the
    # manager atomically decides whether this dispatch had actually started.
    task.status = TaskStatus.CANCELLING.value
    task.message = "Stopping current work"
    db.commit()
    if not task_manager.cancel(task.id):
        task.status = TaskStatus.CANCELLED.value
        task.current_stage = None
        task.message = "Cancelled"
        task.finished_at = datetime.now(timezone.utc)
        db.commit()
    db.refresh(task)
    return task


@router.post("/projects/{project_id}/batch-process", response_model=list[TaskRead], status_code=202)
def batch_process(project_id: str, payload: BatchProcessRequest, db: Session = Depends(get_db)) -> list[ProcessingTask]:
    require_project(db, project_id)
    query = select(ImagePage).where(ImagePage.project_id == project_id).options(selectinload(ImagePage.regions))
    if payload.image_ids is not None:
        query = query.where(ImagePage.id.in_(payload.image_ids))
    pages = list(db.scalars(query.order_by(ImagePage.order_index)).all())
    if payload.image_ids is not None and len(pages) != len(set(payload.image_ids)):
        raise HTTPException(404, "One or more selected pages were not found in this project")
    if payload.only_unrecognized:
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
        pages = [page for page in pages if page.id not in active_image_ids and _page_needs_ocr(page)]
    tasks: list[ProcessingTask] = []
    task_payload = payload.model_dump(mode="json", exclude={"image_ids", "only_unrecognized"})
    for page in pages:
        task = ProcessingTask(
            project_id=project_id,
            image_id=page.id,
            task_type="full",
            status=TaskStatus.QUEUED.value,
            payload=task_payload,
            message="Queued by batch process",
        )
        db.add(task)
        tasks.append(task)
    db.commit()
    for task in tasks:
        db.refresh(task)
        task_manager.dispatch(task.id)
    return tasks


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

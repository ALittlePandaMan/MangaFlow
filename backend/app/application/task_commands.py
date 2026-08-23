from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ImagePage, ProcessingTask
from app.models.enums import TaskStatus
from app.schemas.domain import ProcessRequest
from app.tasks import task_manager


def enqueue_page_task(
    db: Session,
    page: ImagePage,
    stage: str,
    payload: ProcessRequest,
    region_id: str | None = None,
    start_stage: str | None = None,
) -> ProcessingTask:
    """Persist and dispatch a page task without coupling one API route to another."""
    task = ProcessingTask(
        project_id=page.project_id,
        image_id=page.id,
        region_id=region_id,
        task_type=stage,
        status=TaskStatus.QUEUED.value,
        payload={**payload.model_dump(mode="json"), "start_stage": start_stage or stage, "end_stage": stage},
        message="Queued",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    task_manager.dispatch(task.id)
    return task

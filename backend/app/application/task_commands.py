from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ImagePage, ProcessingTask
from app.models.enums import TaskStatus
from app.schemas.domain import ProcessRequest
from app.services.mcp_context import commit_page_tasks
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
    project_id, page_id = page.project_id, page.id
    values = payload.model_dump(mode="json")
    if stage != "full":
        values.update(start_stage=start_stage or stage, end_stage=stage)

    def create(versions):
        task = ProcessingTask(project_id=project_id, image_id=page_id, region_id=region_id,
            task_type=stage, status=TaskStatus.QUEUED.value,
            payload={**values, **({"_mcp_admission_version": versions[page_id]} if versions else {})}, message="Queued")
        db.add(task)
        return task

    task = commit_page_tasks(db, project_id, [page_id], create)
    db.refresh(task)
    task_manager.dispatch(task.id)
    return task

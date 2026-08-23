from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.helpers import image_read, require_page
from app.core.database import get_db
from app.models import ImagePage, ProcessingTask
from app.models.enums import PageStatus, TaskStatus
from app.schemas.domain import ImageRead, ProcessRequest, TaskRead
from app.storage import get_storage
from app.tasks import task_manager

router = APIRouter(tags=["images"])


@router.get("/images/{image_id}", response_model=ImageRead)
def get_image(image_id: str, db: Session = Depends(get_db)) -> ImageRead:
    return image_read(require_page(db, image_id))


@router.delete("/images/{image_id}", status_code=204)
def delete_image(image_id: str, db: Session = Depends(get_db)) -> None:
    page = require_page(db, image_id)
    directory = get_storage().page_dir(page.project_id, page.id)
    db.delete(page)
    db.commit()
    if directory.exists():
        shutil.rmtree(directory)


@router.post("/images/{image_id}/reset", response_model=ImageRead)
def reset_image(image_id: str, db: Session = Depends(get_db)) -> ImageRead:
    page = require_page(db, image_id)
    active_task = db.scalar(
        select(ProcessingTask.id).where(
            ProcessingTask.image_id == image_id,
            ProcessingTask.status.in_(
                [TaskStatus.QUEUED.value, TaskStatus.RUNNING.value, TaskStatus.PAUSED.value]
            ),
        )
    )
    if active_task:
        raise HTTPException(409, "Wait for the current processing task to finish before resetting the page")

    for region in list(page.regions):
        db.delete(region)
    page.clean_path = None
    page.rendered_path = None
    page.text_layer_path = None
    page.status = PageStatus.UPLOADED.value
    page.current_stage = None
    page.error_message = None
    page.metadata_json = {}
    db.commit()

    page_directory = get_storage().page_dir(page.project_id, page.id)
    for name in ("masks", "clean", "rendered", "layers", "versions"):
        directory = page_directory / name
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
    db.refresh(page)
    return image_read(page)


@router.patch("/images/{image_id}/order", response_model=ImageRead)
def reorder_image(image_id: str, order_index: int, db: Session = Depends(get_db)) -> ImageRead:
    page = require_page(db, image_id)
    if order_index < 0:
        raise HTTPException(422, "order_index cannot be negative")
    other = db.scalar(
        select(ImagePage).where(
            ImagePage.project_id == page.project_id,
            ImagePage.order_index == order_index,
            ImagePage.id != page.id,
        )
    )
    if other:
        previous_index = page.order_index
        temporary_index = (db.scalar(
            select(func.max(ImagePage.order_index)).where(ImagePage.project_id == page.project_id)
        ) or 0) + 1
        other.order_index = temporary_index
        db.flush()
        page.order_index = order_index
        db.flush()
        other.order_index = previous_index
    else:
        page.order_index = order_index
    db.commit()
    db.refresh(page)
    return image_read(page)


def enqueue_page_task(
    db: Session,
    page: ImagePage,
    stage: str,
    payload: ProcessRequest,
    region_id: str | None = None,
    start_stage: str | None = None,
) -> ProcessingTask:
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


@router.post("/images/{image_id}/process", response_model=TaskRead, status_code=202)
def process_image(image_id: str, payload: ProcessRequest, db: Session = Depends(get_db)) -> ProcessingTask:
    page = require_page(db, image_id)
    task = ProcessingTask(
        project_id=page.project_id,
        image_id=page.id,
        task_type="full",
        status=TaskStatus.QUEUED.value,
        payload=payload.model_dump(mode="json"),
        message="Queued",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    task_manager.dispatch(task.id)
    return task


def _stage_endpoint(stage: str):
    def endpoint(image_id: str, payload: ProcessRequest, db: Session = Depends(get_db)) -> ProcessingTask:
        return enqueue_page_task(db, require_page(db, image_id), stage, payload)

    endpoint.__name__ = f"run_{stage}"
    return endpoint


for _stage in ("detect", "ocr", "translate", "inpaint", "render"):
    _internal = {"detect": "detection", "translate": "translation", "render": "rendering"}.get(_stage, _stage)
    router.add_api_route(
        f"/images/{{image_id}}/{_stage}",
        _stage_endpoint(_internal),
        methods=["POST"],
        response_model=TaskRead,
        status_code=202,
    )

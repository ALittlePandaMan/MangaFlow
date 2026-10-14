from __future__ import annotations

import shutil

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.helpers import image_read, require_page
from app.application import enqueue_page_task
from app.core.database import get_db
from app.models import ImagePage, ProcessingTask
from app.models.enums import PageStatus, TaskStatus
from app.schemas.domain import ImageRead, ProcessRequest, TaskRead
from app.services.editor_history import capture_page_state, capture_project_state, record_page_edit, record_project_edit
from app.services.image_previews import PreviewKind, image_preview, preview_source
from app.storage import get_storage
from app.tasks import task_manager

router = APIRouter(tags=["images"])


@router.get("/images/{image_id}/preview/{kind}")
def get_image_preview(image_id: str, kind: PreviewKind, v: str = "", db: Session = Depends(get_db)) -> FileResponse:
    page = require_page(db, image_id)
    try:
        path = image_preview(page, kind)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Preview source not found") from exc
    _, version = preview_source(page, kind)
    # A stale request may race an edit/undo. Do not cache its latest pixels under
    # an obsolete URL; the refreshed page response will supply the new version.
    cache = "public, max-age=31536000, immutable" if v == version else "no-cache"
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": cache})


@router.get("/images/{image_id}", response_model=ImageRead)
def get_image(image_id: str, db: Session = Depends(get_db)) -> ImageRead:
    return image_read(require_page(db, image_id), include_history=True)


@router.delete("/images/{image_id}", status_code=204)
def delete_image(image_id: str, db: Session = Depends(get_db)) -> None:
    page = require_page(db, image_id)
    directory = get_storage().page_dir(page.project_id, page.id)
    project_id = page.project_id
    before = capture_project_state(db, project_id, [page.id], editing=True)
    db.delete(page)
    record_project_edit(db, project_id, before, "删除图片", [image_id], image_id)
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
                [
                    TaskStatus.QUEUED.value,
                    TaskStatus.RUNNING.value,
                    TaskStatus.CANCELLING.value,
                    TaskStatus.PAUSED.value,
                ]
            ),
        )
    )
    if active_task:
        raise HTTPException(409, "Wait for the current processing task to finish before resetting the page")
    before = capture_page_state(db, page, editing=True)

    for region in list(page.regions):
        db.delete(region)
    page.clean_path = None
    page.rendered_path = None
    page.text_layer_path = None
    page.status = PageStatus.UPLOADED.value
    page.current_stage = None
    page.error_message = None
    page.metadata_json = {}
    record_page_edit(db, page, before, "重置当前页", [region["id"] for region in before["regions"]])
    db.commit()

    page_directory = get_storage().page_dir(page.project_id, page.id)
    for name in ("masks", "bubbles", "clean", "rendered", "layers", "versions"):
        directory = page_directory / name
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True, exist_ok=True)
    db.refresh(page)
    return image_read(page)


@router.patch("/images/{image_id}/ocr-exempt", response_model=ImageRead)
def set_image_ocr_exempt(image_id: str, exempt: bool, db: Session = Depends(get_db)) -> ImageRead:
    page = require_page(db, image_id)
    active_task = db.scalar(
        select(ProcessingTask.id).where(
            ProcessingTask.image_id == image_id,
            ProcessingTask.status.in_([
                TaskStatus.QUEUED.value,
                TaskStatus.RUNNING.value,
                TaskStatus.CANCELLING.value,
                TaskStatus.PAUSED.value,
            ]),
        )
    )
    if active_task:
        raise HTTPException(409, "Wait for the current processing task to finish before changing OCR status")
    before = capture_page_state(db, page, editing=True)
    metadata = dict(page.metadata_json or {})
    if exempt:
        metadata["ocr_exempt"] = True
    else:
        metadata.pop("ocr_exempt", None)
    page.metadata_json = metadata
    record_page_edit(db, page, before, "修改页面翻译状态")
    db.commit()
    db.refresh(page)
    return image_read(page)


@router.patch("/images/{image_id}/order", response_model=ImageRead)
def reorder_image(image_id: str, order_index: int, db: Session = Depends(get_db)) -> ImageRead:
    page = require_page(db, image_id)
    if order_index < 0:
        raise HTTPException(422, "order_index cannot be negative")
    before = capture_project_state(db, page.project_id, [], editing=True)
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
    record_project_edit(db, page.project_id, before, "调整图片顺序", [], page.id)
    db.commit()
    db.refresh(page)
    return image_read(page)


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
    _internal = {
        "detect": "detection",
        "translate": "translation",
        "inpaint": "inpainting",
        "render": "rendering",
    }.get(_stage, _stage)
    router.add_api_route(
        f"/images/{{image_id}}/{_stage}",
        _stage_endpoint(_internal),
        methods=["POST"],
        response_model=TaskRead,
        status_code=202,
    )

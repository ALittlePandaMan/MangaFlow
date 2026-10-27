from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.helpers import image_read, require_page
from app.application import enqueue_page_task
from app.core.database import get_db
from app.models import ImagePage, ProcessingTask
from app.models.enums import PageStatus, TaskStatus
from app.schemas.domain import ImageRead, ProcessRequest, TaskRead
from app.services.image_previews import PreviewKind, image_preview, preview_source
from app.services.page_edits import apply_page_order, prepare_project_edit, publish_project_edit
from app.services.region_edits import apply_page_edit

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
    project_id = page.project_id
    prepared = prepare_project_edit(db, project_id, [image_id])
    before = prepared["before"]
    after = {**before, "pages": [], "order": [item for item in before["order"] if item["id"] != image_id]}
    publish_project_edit(db, prepared, after, lambda writer: writer.delete(writer.get(ImagePage, image_id)),
                         "删除图片", image_id)


@router.post("/images/{image_id}/reset", response_model=ImageRead)
def reset_image(image_id: str, db: Session = Depends(get_db)) -> ImageRead:
    require_page(db, image_id)
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
    def reset(private, current):
        identifiers = [region.id for region in current.regions]
        for region in list(current.regions):
            private.delete(region)
        current.clean_path = current.rendered_path = current.text_layer_path = None
        current.status, current.current_stage, current.error_message = PageStatus.UPLOADED.value, None, None
        current.metadata_json = {}
        return {"region_ids": identifiers}
    apply_page_edit(db, image_id, reset, "重置当前页")
    # Old immutable files remain available to undo. Do not race a newly
    # admitted task by deleting its page directories after releasing the lock.
    return image_read(require_page(db, image_id))


@router.patch("/images/{image_id}/ocr-exempt", response_model=ImageRead)
def set_image_ocr_exempt(image_id: str, exempt: bool, db: Session = Depends(get_db)) -> ImageRead:
    require_page(db, image_id)
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
    def change(_private, current):
        metadata = dict(current.metadata_json or {})
        if exempt:
            metadata["ocr_exempt"] = True
        else:
            metadata.pop("ocr_exempt", None)
        current.metadata_json = metadata
        return {"region_ids": []}
    apply_page_edit(db, image_id, change, "修改页面翻译状态", scope=[])
    return image_read(require_page(db, image_id))


@router.patch("/images/{image_id}/order", response_model=ImageRead)
def reorder_image(image_id: str, order_index: int, db: Session = Depends(get_db)) -> ImageRead:
    page = require_page(db, image_id)
    if order_index < 0:
        raise HTTPException(422, "order_index cannot be negative")
    project_id = page.project_id
    prepared = prepare_project_edit(db, project_id, [])
    before = prepared["before"]
    previous_index = next((item["order_index"] for item in before["order"] if item["id"] == image_id), None)
    if previous_index is None:
        raise HTTPException(409, "图片列表已变化，请刷新后重试")
    order = [{**item, "order_index": order_index if item["id"] == image_id else
              previous_index if item["order_index"] == order_index else item["order_index"]}
             for item in before["order"]]
    publish_project_edit(db, prepared, {**before, "order": order},
        lambda writer: apply_page_order(writer, project_id, order), "调整图片顺序", image_id)
    return image_read(require_page(db, image_id))


@router.post("/images/{image_id}/process", response_model=TaskRead, status_code=202)
def process_image(image_id: str, payload: ProcessRequest, db: Session = Depends(get_db)) -> ProcessingTask:
    return enqueue_page_task(db, require_page(db, image_id), "full", payload)


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

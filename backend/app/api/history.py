from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.helpers import image_read, region_read, require_page, require_project
from app.core.database import get_db
from app.models import ImagePage
from app.services.editor_history import apply_project_history, history_state, project_history_state

router = APIRouter(tags=["editor history"])


class HistoryRequest(BaseModel):
    expected_entry_id: int = Field(gt=0)
    current_image_id: str | None = None
    operation_id:str | None=Field(default=None,min_length=1,max_length=80)


@router.get("/images/{image_id}/history")
def get_history(image_id: str, db: Session = Depends(get_db)):
    require_page(db, image_id)
    return history_state(db, image_id)


@router.post("/images/{image_id}/history/{direction}")
def apply_history(image_id: str, direction: Literal["undo", "redo"], payload: HistoryRequest, db: Session = Depends(get_db)):
    page = require_page(db, image_id)
    return apply_project_edit_history(page.project_id, direction, payload, db)


@router.get("/projects/{project_id}/history")
def get_project_history(project_id: str, db: Session = Depends(get_db)):
    require_project(db, project_id)
    return project_history_state(db, project_id)


@router.post("/projects/{project_id}/history/{direction}")
def apply_project_edit_history(project_id: str, direction: Literal["undo", "redo"], payload: HistoryRequest, db: Session = Depends(get_db)):
    require_project(db, project_id)
    entry = apply_project_history(db, project_id, direction, payload.expected_entry_id,operation_id=payload.operation_id)
    pages = list(db.scalars(select(ImagePage).where(ImagePage.project_id == project_id).order_by(ImagePage.order_index)).all())
    current = next((item for item in pages if item.id == payload.current_image_id), None)
    # Reordering does not change the editing page. Content commands focus their
    # affected page so an undo on another page is visible rather than silent.
    focus_id = None if entry.scope == "project" and not entry.before["tracked_page_ids"] else entry.image_id
    page = next((item for item in pages if item.id == focus_id), None) or current
    if page is None and pages:
        previous_index = next((item["order_index"] for item in entry.before.get("order", []) if item["id"] == entry.image_id), 0)
        page = min(pages, key=lambda item: abs(item.order_index - previous_index))
    return {"page": image_read(page, include_history=True) if page else None,
        "pages": [image_read(item) for item in pages], "regions": [region_read(region) for region in page.regions] if page else [],
        "history": project_history_state(db, project_id), "region_ids": entry.region_ids, "action": entry.action}

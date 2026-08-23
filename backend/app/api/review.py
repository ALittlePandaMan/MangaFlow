from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.helpers import require_project
from app.core.database import get_db
from app.models import ImagePage, ProcessingTask
from app.models.enums import TaskStatus
from app.schemas.domain import QualityIssue
from app.services.quality import evaluate_page

router = APIRouter(tags=["quality"])


@router.get("/review", response_model=list[QualityIssue])
def needs_review(project_id: str | None = None, db: Session = Depends(get_db)) -> list[QualityIssue]:
    if project_id:
        require_project(db, project_id)
    query = select(ImagePage).options(selectinload(ImagePage.regions))
    if project_id:
        query = query.where(ImagePage.project_id == project_id)
    pages = list(db.scalars(query.order_by(ImagePage.project_id, ImagePage.order_index)).all())
    issues: list[QualityIssue] = []
    for page in pages:
        failed = list(
            db.scalars(
                select(ProcessingTask).where(
                    ProcessingTask.image_id == page.id,
                    ProcessingTask.status == TaskStatus.FAILED.value,
                )
            ).all()
        )
        issues.extend(evaluate_page(page, failed))
    db.commit()
    return issues


@router.get("/projects/{project_id}/review", response_model=list[QualityIssue])
def project_review(project_id: str, db: Session = Depends(get_db)) -> list[QualityIssue]:
    return needs_review(project_id, db)

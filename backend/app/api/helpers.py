from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import ImagePage, ProcessingTask, Project, TextRegion
from app.schemas.domain import ImageRead, ProjectRead, RegionRead
from app.storage import get_storage


def require_project(db: Session, project_id: str) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "Project not found")
    return project


def require_page(db: Session, image_id: str) -> ImagePage:
    page = db.get(ImagePage, image_id)
    if page is None:
        raise HTTPException(404, "Image page not found")
    return page


def require_region(db: Session, region_id: str) -> TextRegion:
    region = db.get(TextRegion, region_id)
    if region is None:
        raise HTTPException(404, "Text region not found")
    return region


def require_task(db: Session, task_id: str) -> ProcessingTask:
    task = db.get(ProcessingTask, task_id)
    if task is None:
        raise HTTPException(404, "Task not found")
    return task


def project_read(project: Project) -> ProjectRead:
    result = ProjectRead.model_validate(project)
    cover_page = min(project.pages, key=lambda page: page.order_index, default=None)
    cover_url = get_storage().media_url(cover_page.original_path) if cover_page else None
    if cover_url and cover_page:
        cover_url = f"{cover_url}?v={int(cover_page.created_at.timestamp() * 1000)}"
    return result.model_copy(update={"page_count": len(project.pages), "cover_url": cover_url})


def image_read(page: ImagePage) -> ImageRead:
    storage = get_storage()
    result = ImageRead.model_validate(page)
    artifact_versions = (page.metadata_json or {}).get("artifact_versions", {})

    def versioned(path: str | None, artifact: str) -> str | None:
        url = storage.media_url(path)
        if not url or not path:
            return None
        # Original files never change after upload. Generated artifacts record
        # their own version when written; legacy imports fall back to the page
        # timestamp without paying for many cross-filesystem stat() calls in a
        # large project listing.
        version = (
            int(page.created_at.timestamp() * 1000)
            if artifact == "original"
            else artifact_versions.get(artifact) or int(page.updated_at.timestamp() * 1000)
        )
        return f"{url}?v={version}"

    return result.model_copy(
        update={
            "ocr_exempt": bool((page.metadata_json or {}).get("ocr_exempt", False)),
            "original_url": versioned(page.original_path, "original"),
            "clean_url": versioned(page.clean_path, "clean"),
            "rendered_url": versioned(page.rendered_path, "rendered"),
            "text_layer_url": versioned(page.text_layer_path, "text_layer"),
        }
    )


def region_read(region: TextRegion) -> RegionRead:
    result = RegionRead.model_validate(region)
    return result.model_copy(update={"mask_url": get_storage().media_url(region.pixel_mask_path)})

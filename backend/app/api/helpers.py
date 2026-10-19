from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session, object_session

from app.models import ImagePage, ProcessingTask, Project, TextRegion
from app.schemas.domain import ImageRead, ProjectRead, RegionRead
from app.services.image_previews import preview_url
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


def project_read(project: Project, *, page_count: int | None = None, cover_page: ImagePage | None = None) -> ProjectRead:
    result = ProjectRead.model_validate(project)
    if page_count is None:
        page_count = len(project.pages)
        cover_page = min(project.pages, key=lambda page: page.order_index, default=None)
    cover_url = get_storage().media_url(cover_page.original_path) if cover_page else None
    if cover_url and cover_page:
        cover_url = f"{cover_url}?v={int(cover_page.created_at.timestamp() * 1000)}"
    return result.model_copy(update={"page_count": page_count, "cover_url": cover_url,
        "cover_thumbnail_url": preview_url(cover_page, "cover") if cover_page else None})


def image_read(page: ImagePage, *, include_history: bool = False, region_count: int | None = None,
               metadata: dict | None = None) -> ImageRead:
    from app.services.editor_history import history_state
    storage = get_storage()
    result = ImageRead.model_validate(page)
    if region_count is None:
        db = object_session(page)
        region_count = db.scalar(select(func.count()).select_from(TextRegion).where(TextRegion.image_id == page.id)) if db else len(page.regions)
    metadata = (page.metadata_json or {}) if metadata is None else metadata
    classification = metadata.get("text_classification")
    classification_summary = {
        key: classification[key]
        for key in ("version", "status", "excluded_count", "uncertain_count", "policy")
        if key in classification
    } if isinstance(classification, dict) else None
    artifact_versions = metadata.get("artifact_versions") or {}

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
            "text_classification": classification_summary,
            "editor_history": history_state(object_session(page), page.id) if include_history and object_session(page) else None,
            "ocr_exempt": bool(metadata.get("ocr_exempt", False)),
            "region_count": region_count,
            "original_url": versioned(page.original_path, "original"),
            "thumbnail_url": preview_url(page, "page", metadata=metadata),
            "thumbnail_2x_url": preview_url(page, "page-2x", metadata=metadata),
            "clean_url": versioned(page.clean_path, "clean"),
            "rendered_url": versioned(page.rendered_path, "rendered"),
            "text_layer_url": versioned(page.text_layer_path, "text_layer"),
            "text_layer_region_ids": metadata.get("text_layer_region_ids") if page.text_layer_path else None,
        }
    )


def region_read(region: TextRegion) -> RegionRead:
    from app.services.editor_history import history_state
    result = RegionRead.model_validate(region)
    db = object_session(region)
    layout = dict(region.layout_data or {})
    generation = dict(layout.get("mask_generation", {}))
    if (generation.get("method") == "manual_mask" and "repair_pending" not in generation
            and region.pixel_mask_path and not region.inpainted_path):
        # Older versions cleared inpainted_path after each mask edit but did
        # not store a pending flag. Expose it without mutating a read request.
        layout["mask_generation"] = {**generation, "repair_pending": True}
    return result.model_copy(update={"mask_url": get_storage().media_url(region.pixel_mask_path),
        "layout_data": layout,
        "editor_history": history_state(db, region.image_id) if db else None})

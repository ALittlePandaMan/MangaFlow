from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.api.helpers import image_read, project_read, require_project
from app.core.config import get_settings
from app.core.database import get_db
from app.models import ImagePage, ProcessingTask, Project
from app.models.enums import TaskStatus
from app.pipeline.processor import PipelineProcessor
from app.schemas.domain import ExportRequest, ImageRead, ProjectCreate, ProjectRead, ProjectUpdate
from app.services.exporting import export_project, import_project_archive
from app.services.rendering.pillow_renderer import RENDER_OUTPUT_VERSION
from app.storage import StorageError, get_storage

router = APIRouter(tags=["projects"])


@router.post("/projects", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectRead:
    project = Project(**payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
    return project_read(project)


@router.post("/projects/import", response_model=ProjectRead, status_code=201)
def import_project(file: UploadFile = File(...), db: Session = Depends(get_db)) -> ProjectRead:
    if Path(file.filename or "").suffix.lower() != ".zip":
        raise HTTPException(422, "请选择 MangaFlow 导出的 ZIP 项目包")
    maximum = get_settings().max_upload_mb * 1024 * 1024 * 10
    if file.size is not None and file.size > maximum:
        raise HTTPException(413, "项目包体积超过导入限制")
    try:
        project = import_project_archive(
            file.file,
            db,
            get_storage(),
            maximum_uncompressed_bytes=maximum,
        )
    except StorageError as exc:
        raise HTTPException(400, str(exc)) from exc
    return project_read(project)


@router.get("/projects", response_model=list[ProjectRead])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectRead]:
    projects = list(
        db.scalars(select(Project).options(selectinload(Project.pages)).order_by(Project.updated_at.desc())).all()
    )
    return [project_read(project) for project in projects]


@router.get("/projects/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, db: Session = Depends(get_db)) -> ProjectRead:
    project = db.scalar(select(Project).where(Project.id == project_id).options(selectinload(Project.pages)))
    if project is None:
        raise HTTPException(404, "Project not found")
    return project_read(project)


@router.patch("/projects/{project_id}", response_model=ProjectRead)
def update_project(project_id: str, payload: ProjectUpdate, db: Session = Depends(get_db)) -> ProjectRead:
    project = require_project(db, project_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, field, value)
    db.commit()
    db.refresh(project)
    return project_read(project)


@router.delete("/projects/{project_id}", status_code=204)
def delete_project(project_id: str, db: Session = Depends(get_db)) -> None:
    project = require_project(db, project_id)
    db.delete(project)
    db.commit()
    get_storage().remove_project(project_id)


@router.post("/projects/{project_id}/images", response_model=list[ImageRead], status_code=201)
def upload_images(
    project_id: str,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
) -> list[ImageRead]:
    require_project(db, project_id)
    if not files:
        raise HTTPException(400, "At least one image is required")
    storage = get_storage()
    maximum = get_settings().max_upload_mb * 1024 * 1024
    current_max_order = db.scalar(select(func.max(ImagePage.order_index)).where(ImagePage.project_id == project_id))
    next_order = (current_max_order if current_max_order is not None else -1) + 1
    pages: list[ImagePage] = []
    created_page_ids: list[str] = []

    def cleanup_created_pages() -> None:
        for created_page_id in created_page_ids:
            page_dir = storage.project_dir(project_id) / "pages" / created_page_id
            if page_dir.exists():
                shutil.rmtree(page_dir)

    try:
        for offset, upload in enumerate(files):
            if upload.size is not None and upload.size > maximum:
                raise StorageError(f"{upload.filename} exceeds the {get_settings().max_upload_mb} MB limit")
            page_id = str(uuid.uuid4())
            created_page_ids.append(page_id)
            relative, width, height = storage.save_upload(
                project_id, page_id, upload.filename or "page.png", upload.file
            )
            page = ImagePage(
                id=page_id,
                project_id=project_id,
                filename=Path(upload.filename or "page.png").name,
                original_path=relative,
                width=width,
                height=height,
                order_index=next_order + offset,
            )
            db.add(page)
            pages.append(page)
        db.commit()
    except StorageError as exc:
        db.rollback()
        cleanup_created_pages()
        raise HTTPException(400, str(exc)) from exc
    except Exception:
        db.rollback()
        cleanup_created_pages()
        raise
    for page in pages:
        db.refresh(page)
    return [image_read(page) for page in pages]


@router.get("/projects/{project_id}/images", response_model=list[ImageRead])
def list_images(project_id: str, db: Session = Depends(get_db)) -> list[ImageRead]:
    require_project(db, project_id)
    pages = list(
        db.scalars(select(ImagePage).where(ImagePage.project_id == project_id).order_by(ImagePage.order_index)).all()
    )
    return [image_read(page) for page in pages]


@router.get("/projects/{project_id}/fonts")
def list_project_fonts(project_id: str, db: Session = Depends(get_db)) -> list[dict]:
    project = require_project(db, project_id)
    return list((project.settings or {}).get("fonts", []))


@router.post("/projects/{project_id}/fonts", status_code=201)
def upload_project_font(project_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict:
    project = require_project(db, project_id)
    original_name = Path(file.filename or "font.otf").name
    if Path(original_name).suffix.lower() not in {".ttf", ".otf"}:
        raise HTTPException(422, "Only TrueType (.ttf) and OpenType (.otf) fonts are supported")
    safe_name = "".join(character if character.isalnum() or character in "._-" else "_" for character in original_name)
    font_dir = get_storage().project_dir(project_id) / "fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    destination = font_dir / safe_name
    with destination.open("wb") as output:
        shutil.copyfileobj(file.file, output)
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(destination, lazy=True)
        family = safe_name.rsplit(".", 1)[0]
        for record in font["name"].names:
            if record.nameID == 1:
                try:
                    family = record.toUnicode()
                    break
                except Exception:
                    continue
        font.close()
    except Exception as exc:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, "The uploaded file is not a valid TrueType/OpenType font") from exc
    entry = {"name": family, "filename": safe_name, "path": get_storage().relative(destination)}
    settings = dict(project.settings or {})
    settings["fonts"] = [item for item in settings.get("fonts", []) if item.get("filename") != safe_name] + [entry]
    project.settings = settings
    db.commit()
    return entry


@router.post("/projects/{project_id}/export")
def export(project_id: str, payload: ExportRequest, db: Session = Depends(get_db)) -> FileResponse:
    project = db.scalar(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.pages).selectinload(ImagePage.regions))
    )
    if project is None:
        raise HTTPException(404, "Project not found")
    storage = get_storage()
    rendered_formats = {"translated", "text_layer"}.intersection(payload.formats)
    pages_to_render = [
        page
        for page in project.pages
        if (
            "translated" in rendered_formats
            and (not page.rendered_path or not storage.absolute(page.rendered_path).is_file())
        )
        or (
            "text_layer" in rendered_formats
            and (not page.text_layer_path or not storage.absolute(page.text_layer_path).is_file())
        )
        or (
            bool(rendered_formats)
            and any(
                region.visible
                and bool(region.translated_text.strip())
                and (region.layout_data or {}).get("render_output_version") != RENDER_OUTPUT_VERSION
                for region in page.regions
            )
        )
    ]
    if pages_to_render:
        active_task = db.scalar(
            select(ProcessingTask.id).where(
                ProcessingTask.project_id == project.id,
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
            raise HTTPException(409, "Wait for the current processing task to finish before exporting")
        processor = PipelineProcessor(db)
        try:
            for page in pages_to_render:
                # Region/style edits intentionally invalidate both cached
                # outputs. Rebuild them from the latest database state so an
                # export cannot silently omit a translated image or text layer.
                processor.render(page, None, region_id=None)
            db.commit()
        except Exception as exc:
            db.rollback()
            raise HTTPException(500, f"Could not rebuild translated output for export: {exc}") from exc
    archive = export_project(project, payload.formats, storage)
    requested = set(payload.formats)
    export_kind = "source-project" if "project" in requested else "translated" if "translated" in requested else "clean" if "clean" in requested else "export"
    safe_name = "".join(character for character in project.name if character.isalnum() or character in "-_. ").strip()[:80] or "mangaflow"
    return FileResponse(archive, filename=f"{safe_name}-{export_kind}.zip", media_type="application/zip")

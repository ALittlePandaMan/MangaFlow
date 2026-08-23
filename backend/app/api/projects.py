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
from app.models import ImagePage, Project
from app.schemas.domain import ExportRequest, ImageRead, ProjectCreate, ProjectRead, ProjectUpdate
from app.services.exporting import export_project
from app.storage import StorageError, get_storage

router = APIRouter(tags=["projects"])


@router.post("/projects", response_model=ProjectRead, status_code=201)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> ProjectRead:
    project = Project(**payload.model_dump())
    db.add(project)
    db.commit()
    db.refresh(project)
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


@router.put("/projects/{project_id}/cover", response_model=ProjectRead)
def upload_project_cover(
    project_id: str,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> ProjectRead:
    project = require_project(db, project_id)
    maximum = get_settings().max_upload_mb * 1024 * 1024
    if file.size is not None and file.size > maximum:
        raise HTTPException(400, f"Cover exceeds the {get_settings().max_upload_mb} MB limit")
    try:
        project.cover_path = get_storage().save_project_cover(
            project_id,
            file.filename or "cover.png",
            file.file,
        )
        db.commit()
        db.refresh(project)
        return project_read(project)
    except StorageError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/projects/{project_id}/cover", response_model=ProjectRead)
def delete_project_cover(project_id: str, db: Session = Depends(get_db)) -> ProjectRead:
    project = require_project(db, project_id)
    if project.cover_path:
        get_storage().absolute(project.cover_path).unlink(missing_ok=True)
    project.cover_path = None
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
    archive = export_project(project, payload.formats, get_storage())
    return FileResponse(archive, filename=f"{project.name}-export.zip", media_type="application/zip")

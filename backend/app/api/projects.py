from __future__ import annotations

import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session, defer, selectinload

from app.api.helpers import image_read, project_read, require_project
from app.core.config import get_settings
from app.core.database import SessionLocal, get_db
from app.models import ImagePage, ProcessingTask, Project, TextRegion
from app.models.enums import TaskStatus
from app.schemas.domain import ExportRequest, ImageRead, ProjectCreate, ProjectRead, ProjectUpdate
from app.services.editor_history import PAGE_FIELDS
from app.services.export_render import refresh_export_page
from app.services.exporting import export_project, import_project_archive
from app.services.page_edits import apply_page_order, prepare_project_edit, publish_project_edit
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
    counts = select(
        ImagePage.project_id, func.count().label("count"), func.min(ImagePage.order_index).label("first_order")
    ).group_by(ImagePage.project_id).subquery()
    rows = db.execute(
        select(Project, counts.c.count, ImagePage)
        .outerjoin(counts, counts.c.project_id == Project.id)
        .outerjoin(ImagePage, and_(ImagePage.project_id == Project.id, ImagePage.order_index == counts.c.first_order))
        .order_by(Project.updated_at.desc())
    )
    return [project_read(project, page_count=count or 0, cover_page=cover) for project, count, cover in rows]


@router.get("/projects/{project_id}", response_model=ProjectRead)
def get_project(project_id: str, db: Session = Depends(get_db)) -> ProjectRead:
    project = require_project(db, project_id)
    count = db.scalar(select(func.count()).select_from(ImagePage).where(ImagePage.project_id == project_id))
    cover = db.scalar(select(ImagePage).where(ImagePage.project_id == project_id).order_by(ImagePage.order_index).limit(1))
    return project_read(project, page_count=count or 0, cover_page=cover)


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
    prepared = prepare_project_edit(db, project_id, [])
    before = prepared["before"]
    storage = get_storage()
    maximum = get_settings().max_upload_mb * 1024 * 1024
    next_order = max((item["order_index"] for item in before["order"]), default=-1) + 1
    values = []
    created_page_ids: list[str] = []

    def cleanup_created_pages() -> None:
        # A lost COMMIT acknowledgement is ambiguous. Never remove originals
        # that may already be referenced by committed rows, or when the DB is
        # unavailable and absence cannot be proved with a fresh connection.
        try:
            with SessionLocal() as probe:
                if probe.scalar(select(ImagePage.id).where(ImagePage.id.in_(created_page_ids)).limit(1)):
                    return
        except Exception:
            return
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
            values.append(dict(id=page_id, project_id=project_id, filename=Path(upload.filename or "page.png").name,
                original_path=relative, width=width, height=height, order_index=next_order + offset,
                clean_path=None, rendered_path=None, text_layer_path=None, status="UPLOADED", error_message=None,
                metadata_json={}))
        prepared["before"] = {**before, "tracked_page_ids": created_page_ids}
        after = {"pages": [{"page": {key: row[key] for key in PAGE_FIELDS}, "regions": []} for row in values],
                 "tracked_page_ids": created_page_ids,
                 "order": before["order"] + [{"id": row["id"], "order_index": row["order_index"]} for row in values]}
        publish_project_edit(db, prepared, after, lambda writer: writer.add_all(ImagePage(**row) for row in values),
                             "导入图片", created_page_ids[0])
    except StorageError as exc:
        db.rollback()
        cleanup_created_pages()
        raise HTTPException(400, str(exc)) from exc
    except Exception:
        db.rollback()
        cleanup_created_pages()
        raise
    return [image_read(db.get(ImagePage, identifier)) for identifier in created_page_ids]


@router.get("/projects/{project_id}/images", response_model=list[ImageRead])
def list_images(project_id: str, db: Session = Depends(get_db)) -> list[ImageRead]:
    require_project(db, project_id)
    # Detector reports can dwarf the index itself. Project only the four small
    # metadata fields used by UI summaries and stable artifact URLs, never the
    # complete detection/mask diagnostics for every page in a large book.
    keys = ("artifact_versions", "text_classification", "ocr_exempt", "text_layer_region_ids")
    pages = db.execute(select(ImagePage, *(ImagePage.metadata_json[key] for key in keys))
        .options(defer(ImagePage.metadata_json, raiseload=True))
        .where(ImagePage.project_id == project_id).order_by(ImagePage.order_index)).all()
    # One aggregate query, not a region-list request (or ORM lazy load) per page.
    counts = dict(db.execute(
        # Count rows without loading large text-region payloads.
        select(TextRegion.image_id, func.count())
        .join(ImagePage, ImagePage.id == TextRegion.image_id)
        .where(ImagePage.project_id == project_id).group_by(TextRegion.image_id)
    ).all())
    return [image_read(row[0], region_count=counts.get(row[0].id, 0),
                      metadata=dict(zip(keys, row[1:], strict=True))) for row in pages]


@router.post("/projects/{project_id}/images/order", response_model=list[ImageRead])
def reorder_images(project_id: str, image_ids: list[str], db: Session = Depends(get_db)):
    require_project(db, project_id)
    prepared = prepare_project_edit(db, project_id, [])
    before = prepared["before"]
    pages = {item["id"] for item in before["order"]}
    if len(set(image_ids)) != len(image_ids) or set(image_ids) != set(pages):
        raise HTTPException(409, "图片列表已变化，请刷新后重新排序")
    order = [{"id": image_id, "order_index": index} for index, image_id in enumerate(image_ids)]
    publish_project_edit(db, prepared, {**before, "order": order},
        lambda writer: apply_page_order(writer, project_id, order), "调整图片顺序", image_ids[0] if image_ids else None)
    return [image_read(db.get(ImagePage, image_id)) for image_id in image_ids]


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
    pages_to_render = [page.id for page in project.pages if _needs_export_render(page, rendered_formats, storage)]
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
        try:
            for identifier in pages_to_render:
                # Region/style edits intentionally invalidate both cached
                # outputs. Rebuild them from the latest database state so an
                # export cannot silently omit a translated image or text layer.
                refresh_export_page(db, identifier)
        except HTTPException:
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(500, f"Could not rebuild translated output for export: {exc}") from exc
    project = _export_snapshot(db, project_id)
    # Earlier pages can be edited while later pages render. Never silently
    # omit an invalidated output, or zip new text with an older image.
    if any(_needs_export_render(page, rendered_formats, storage) for page in project.pages):
        raise HTTPException(409, "导出期间页面已变化，请重试；未导出不完整的图片")
    archive = export_project(project, payload.formats, storage)
    requested = set(payload.formats)
    export_kind = "source-project" if "project" in requested else "translated" if "translated" in requested else "clean" if "clean" in requested else "export"
    safe_name = "".join(character for character in project.name if character.isalnum() or character in "-_. ").strip()[:80] or "mangaflow"
    return FileResponse(archive, filename=f"{safe_name}-{export_kind}.zip", media_type="application/zip")


def _needs_export_render(page, formats, storage):
    return (
        ("translated" in formats and (not page.rendered_path or not storage.absolute(page.rendered_path).is_file()))
        or ("text_layer" in formats and (not page.text_layer_path or not storage.absolute(page.text_layer_path).is_file()))
        or (bool(formats) and any(
            region.visible and bool(region.translated_text.strip())
            and (region.layout_data or {}).get("render_output_version") != RENDER_OUTPUT_VERSION
            for region in page.regions
        ))
    )


def _export_snapshot(db, project_id):
    # selectinload executes several SELECTs; READ COMMITTED can mix revisions.
    # Hold a consistent read only for the ORM reads, never for ZIP/file I/O.
    db.rollback()
    try:
        connection = db.connection()
        connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        project = db.scalar(select(Project).where(Project.id == project_id).execution_options(populate_existing=True)
            .options(selectinload(Project.pages).selectinload(ImagePage.regions)))
        if project is None:
            raise HTTPException(404, "Project not found")
        db.expunge_all()
        return project
    finally:
        db.rollback()

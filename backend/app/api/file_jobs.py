from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import case, or_, select
from sqlalchemy.orm import Session, defer

from app.api.helpers import require_project
from app.core.config import get_settings
from app.core.database import get_db
from app.models import FileJob
from app.services.file_jobs import TERMINAL, job_directory, job_read
from app.services.mcp_context import operation_source

router = APIRouter(tags=["file-jobs"])


class FileExportRequest(BaseModel):
    kind: Literal["project", "translated", "clean", "cbz", "psd", "json", "masks", "text_layer"]


@router.post("/projects/{project_id}/imports", status_code=202)
def import_file(project_id: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    require_project(db, project_id)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".zip", ".cbz", ".rar", ".cbr", ".pdf"}:
        raise HTTPException(422, "请选择 ZIP、CBZ、RAR、CBR 或 PDF")
    maximum = get_settings().max_container_upload_mb * 1024**2
    if file.size is not None and file.size > maximum:
        raise HTTPException(413, "文件体积超过导入限制")
    identifier = str(uuid4())
    directory = job_directory(identifier)
    directory.mkdir(parents=True)
    destination = directory / ("input" + suffix)
    try:
        total = 0
        with destination.open("xb") as writer:
            while block := file.file.read(1024**2):
                total += len(block)
                if total > maximum:
                    raise HTTPException(413, "文件体积超过导入限制")
                writer.write(block)
        job = FileJob(id=identifier, project_id=project_id, kind="import", filename=Path(file.filename).name,
            result={"submitted_via": "mcp" if operation_source.get() == "MCP" else "web"})
        db.add(job)
        db.commit()
        return job_read(job)
    except Exception:
        # Preserve staged input after an ambiguous commit; startup collection can
        # remove abandoned directories after the retention window.
        db.rollback()
        raise


@router.post("/projects/{project_id}/exports", status_code=202)
def export_file(project_id: str, payload: FileExportRequest, db: Session = Depends(get_db)):
    project = require_project(db, project_id)
    job = FileJob(project_id=project_id, kind=payload.kind, filename=f"{project.name} · {payload.kind.upper()}",
        result={"submitted_via": "mcp" if operation_source.get() == "MCP" else "web"})
    db.add(job)
    db.commit()
    return job_read(job)


@router.get("/file-jobs")
def list_jobs(project_id: str | None = None,
              limit: int = Query(default=100, ge=1, le=500),
              offset: int = Query(default=0, ge=0), db: Session = Depends(get_db)):
    """All active jobs plus a bounded, paginated window of terminal history.

    limit/offset apply only to history; live work must never disappear behind
    newer submissions. One statement provides a consistent, duplicate-free list.
    """
    filters = []
    query = select(FileJob).options(defer(FileJob.snapshot))
    if project_id:
        require_project(db, project_id)
        filters.append(FileJob.project_id == project_id)
    active = FileJob.status.not_in(TERMINAL)
    history = select(FileJob.id).where(*filters, FileJob.status.in_(TERMINAL)).order_by(
        FileJob.created_at.desc(), FileJob.id.desc()).offset(offset).limit(limit)
    query = query.where(*filters, or_(active, FileJob.id.in_(history))).order_by(
        case((active, 0), else_=1), FileJob.created_at.desc(), FileJob.id.desc())
    return [job_read(job) for job in db.scalars(query)]


@router.post("/file-jobs/status")
def file_job_statuses(identifiers: list[str], db: Session = Depends(get_db)):
    """Read tracked completions beyond the history window; expired IDs are omitted."""
    if not identifiers or len(identifiers) > 400:
        raise HTTPException(422, "Select 1 to 400 file jobs")
    jobs = {job.id: job for job in db.scalars(select(FileJob).options(defer(FileJob.snapshot))
        .where(FileJob.id.in_(set(identifiers))))}
    return [job_read(jobs[identifier]) for identifier in dict.fromkeys(identifiers) if identifier in jobs]


def require_job(db, identifier):
    job = db.get(FileJob, identifier, options=[defer(FileJob.snapshot)])
    if not job:
        raise HTTPException(404, "File job not found")
    return job


@router.get("/file-jobs/{identifier}")
def get_job(identifier: str, db: Session = Depends(get_db)):
    return job_read(require_job(db, identifier))


@router.post("/file-jobs/{identifier}/cancel")
def cancel_job(identifier: str, db: Session = Depends(get_db)):
    job = db.scalar(select(FileJob).where(FileJob.id == identifier).with_for_update())
    if not job:
        raise HTTPException(404, "File job not found")
    if job.status not in TERMINAL:
        job.status = "CANCELLED" if job.status == "QUEUED" else "CANCELLING"
    db.commit()
    return job_read(job)


@router.get("/file-jobs/{identifier}/download")
def download(identifier: str, db: Session = Depends(get_db)):
    job = require_job(db, identifier)
    name = job.result.get("path")
    if job.status != "COMPLETED" or not name or Path(name).name != name:
        raise HTTPException(409, "导出文件尚未准备好")
    path = job_directory(job.id) / name
    if not path.is_file():
        raise HTTPException(410, "导出文件已过期，请重新导出")
    media = "application/vnd.comicbook+zip" if path.suffix == ".cbz" else "application/octet-stream"
    return FileResponse(path, filename=job.result.get("filename", name), media_type=media)

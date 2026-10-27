"""Bounded staging with opaque asset IDs; no host paths or remote URL fetching."""
import re
import time
from datetime import timedelta
from uuid import uuid4

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import MCPAsset
from app.models.entities import utcnow
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy import delete, select, update

router = APIRouter()


def asset_path(identifier):
    if not re.fullmatch(r"[0-9a-f-]{36}", identifier):
        raise HTTPException(404, "Asset not found")
    return get_settings().data_dir / "mcp-assets" / identifier


def require_asset(identifier):
    with SessionLocal() as db:
        asset = db.get(MCPAsset, identifier)
        if not asset or asset.status != "AVAILABLE" or asset.created_at < utcnow() - timedelta(hours=24) or not asset_path(identifier).is_file():
            raise HTTPException(410, "Upload expired; upload the file again")
        return asset


def collect_assets():
    with SessionLocal() as db:
        from app.services.mcp_directory_import import protected_assets
        candidates = list(db.scalars(select(MCPAsset).where(MCPAsset.created_at < utcnow() - timedelta(hours=24))
                                    .with_for_update(skip_locked=True)))
        protected = protected_assets(db)
        expired = [row.id for row in candidates if row.id not in protected]
        for identifier in expired:
            asset_path(identifier).unlink(missing_ok=True)
        db.execute(delete(MCPAsset).where(MCPAsset.id.in_(expired)))
        db.commit()
        # A skipped, locked row still owns its file. Only unregistered files
        # are orphans; file age alone must never bypass a database row lock.
        registered = set(db.scalars(select(MCPAsset.id)))
    root = get_settings().data_dir / "mcp-assets"
    if root.exists():
        for path in root.iterdir():
            try:
                if path.name not in registered and path.name not in protected and path.is_file() and path.stat().st_mtime < (utcnow() - timedelta(hours=24)).timestamp():
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass  # Another collector or an interrupted upload removed it.


@router.post("/mcp/files")
@router.post("/mangaflow/files")
async def upload(request: Request, filename: str):
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not filename or len(filename) > 255 or "\x00" in filename:
        raise HTTPException(422, "Invalid filename")
    identifier = str(uuid4())
    destination = asset_path(identifier)
    destination.parent.mkdir(parents=True, exist_ok=True)
    maximum = get_settings().max_container_upload_mb * 1024**2
    try:
        expected = int(request.headers.get("content-length", "0"))
    except ValueError as exc:
        raise HTTPException(422, "Invalid content length") from exc
    if expected < 0 or expected > maximum:
        raise HTTPException(413, "Upload exceeds configured import limit")
    size = 0
    def publish(status):
        with SessionLocal() as db:
            row = db.get(MCPAsset, identifier)
            if row is None:
                db.add(MCPAsset(id=identifier, filename=filename, size=size, expected_size=expected, status=status))
            else:
                row.size, row.status = size, status
            db.commit()
    try:
        # File writes run off the ASGI event loop, one bounded chunk at a time.
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(publish, "UPLOADING")
        last_progress = time.monotonic()
        with destination.open("xb") as writer:
            async for block in request.stream():
                size += len(block)
                if size > maximum:
                    raise HTTPException(413, "Upload exceeds configured import limit")
                await run_in_threadpool(writer.write, block)
                if time.monotonic() - last_progress >= .5:
                    await run_in_threadpool(publish, "UPLOADING")
                    last_progress = time.monotonic()
        if not size:
            raise HTTPException(422, "Empty upload")
        if expected and expected != size:
            raise HTTPException(422, "Upload was incomplete")
        await run_in_threadpool(publish, "AVAILABLE")
        return {"asset_id": identifier, "filename": filename, "size": size, "expires_in": 86400}
    except BaseException:
        destination.unlink(missing_ok=True)
        try:
            await run_in_threadpool(publish, "FAILED")
        except BaseException:
            # Startup marks interrupted uploads failed; never accept a partial file.
            pass
        raise


def recover_uploads():
    with SessionLocal() as db:
        db.execute(update(MCPAsset).where(MCPAsset.status == "UPLOADING").values(status="FAILED"))
        db.commit()


@router.get("/mcp/download/{identifier}")
@router.get("/mangaflow/download/{identifier}")
def download_export(identifier: str):
    from app.api.file_jobs import download
    with SessionLocal() as db:
        return download(identifier, db)


@router.get("/mcp/previews/{identifier}")
@router.get("/mangaflow/previews/{identifier}")
def download_preview(identifier: str):
    require_asset(identifier)
    return FileResponse(asset_path(identifier), media_type="image/png")


@router.get("/mcp/reports/{identifier}/{kind}")
@router.get("/mangaflow/reports/{identifier}/{kind}")
def download_report(identifier: str, kind: str):
    from app.api.file_jobs import require_job
    from app.services.file_jobs import job_directory
    if kind not in {"json", "markdown"}:
        raise HTTPException(404, "Unknown report format")
    with SessionLocal() as db:
        job = require_job(db, identifier)
        if job.status != "COMPLETED" or not job.result.get("workflow_run_id"):
            raise HTTPException(409, "Workflow report is not ready")
        filename = "review.json" if kind == "json" else "review.md"
        path = job_directory(job.id) / filename
        if not path.is_file():
            raise HTTPException(410, "Workflow report expired")
        return FileResponse(path, filename=filename, media_type="application/json" if kind == "json" else "text/markdown; charset=utf-8")

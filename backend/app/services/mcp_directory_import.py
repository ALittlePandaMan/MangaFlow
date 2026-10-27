"""Import uploaded directory manifests through the existing durable file queue."""
import json
from hashlib import sha256
from pathlib import PurePosixPath

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import FileJob, MCPAsset, MCPOperation, Project
from app.schemas.domain import ProjectCreate
from app.services.file_import import IMAGE_EXTENSIONS, natural_key, safe_member
from app.services.file_jobs import TERMINAL, job_read
from app.services.mcp_files import asset_path
from fastapi import HTTPException
from sqlalchemy import select


def import_limits():
    settings = get_settings()
    return {"max_pages": settings.max_import_pages,
            "max_image_bytes": settings.max_upload_mb * 1024**2,
            "max_container_bytes": settings.max_container_upload_mb * 1024**2,
            "max_total_bytes": settings.max_import_uncompressed_mb * 1024**2,
            "max_page_pixels": settings.max_import_page_pixels}


def lookup(db, operation_id):
    return db.scalar(select(FileJob).where(FileJob.kind == "import",
        FileJob.result["import_operation_id"].as_string() == operation_id))


def status(args):
    from datetime import timedelta

    from app.models.entities import utcnow
    with SessionLocal() as db:
        job = lookup(db, args["operation_id"])
        output = {"operation_id": args["operation_id"], "found": job is not None, "limits": import_limits()}
        if job:
            verify_source(args, job.result.get("source_fingerprint"))
            output.update(project_id=job.project_id, job=job_read(job))
        else:
            receipt = db.get(MCPOperation, args["operation_id"])
            if receipt:
                content = (receipt.result or {}).get("structuredContent", {})
                if content.get("job"):
                    verify_source(args, content.get("source_fingerprint") or content["job"].get("result", {}).get("source_fingerprint"))
                output["operation"] = {"status": receipt.status, "result": receipt.result}
        ids = args.get("asset_ids", [])
        rows = {row.id: row for row in db.scalars(select(MCPAsset).where(MCPAsset.id.in_(ids)))}
        output["available_asset_ids"] = [identifier for identifier in ids if identifier in rows
            and rows[identifier].status == "AVAILABLE" and rows[identifier].created_at >= utcnow()-timedelta(hours=24)
            and asset_path(identifier).is_file()]
        return output


def verify_source(args, actual):
    expected = args.get("source_fingerprint")
    if expected and actual != expected:
        raise HTTPException(409, "Import source fingerprint differs or is unavailable; inspect the original operation instead of recovering another directory")


def submit(args):
    from datetime import timedelta

    from app.models.entities import utcnow
    settings = get_settings()
    manifest = args["images"]
    if not manifest or len(manifest) > settings.max_import_pages:
        raise HTTPException(422, "Directory image count exceeds the configured limit")
    names, ids = set(), set()
    for entry in manifest:
        path = safe_member(entry["relative_path"])
        if path.as_posix() != entry["relative_path"] or any(part.startswith(".") or part == "__MACOSX" for part in path.parts):
            raise HTTPException(422, "Invalid image relative path")
        if path.suffix.lower() not in IMAGE_EXTENSIONS or len(path.name) > 255:
            raise HTTPException(422, "Unsupported image filename")
        if path.as_posix().casefold() in names or entry["asset_id"] in ids:
            raise HTTPException(422, "Duplicate image path or asset")
        names.add(path.as_posix().casefold())
        ids.add(entry["asset_id"])
    manifest = sorted(manifest, key=lambda item: (natural_key(item["relative_path"]), item["relative_path"]))
    contract = {key: args.get(key) for key in ("project_id", "project_name", "source_language", "target_language", "source_fingerprint")}
    digest = sha256(json.dumps({**contract, "images": manifest}, sort_keys=True).encode()).hexdigest()
    with SessionLocal() as db:
        previous = lookup(db, args["operation_id"])
        if previous:
            if previous.result.get("import_manifest_hash") != digest:
                raise HTTPException(409, "Import operation already used for different input")
            return {"project_id": previous.project_id, "job": job_read(previous), "source_fingerprint": previous.result.get("source_fingerprint")}
        assets = list(db.scalars(select(MCPAsset).where(MCPAsset.id.in_(ids)).order_by(MCPAsset.id).with_for_update()))
        if len(assets) != len(ids) or any(asset.status != "AVAILABLE" or asset.created_at < utcnow()-timedelta(hours=24)
                or not asset_path(asset.id).is_file() for asset in assets):
            raise HTTPException(410, "An uploaded image expired or is incomplete; upload it again")
        if any(asset.size > settings.max_upload_mb * 1024**2 for asset in assets) or sum(asset.size for asset in assets) > settings.max_import_uncompressed_mb * 1024**2:
            raise HTTPException(413, "Directory images exceed configured byte limits")
        if args.get("project_id"):
            project = db.get(Project, args["project_id"])
            if not project:
                raise HTTPException(404, "Project not found")
            if any(args.get(key) is not None for key in ("project_name", "source_language", "target_language")):
                raise HTTPException(422, "Existing projects retain their name and languages")
        else:
            values = {key: args[key] for key in ("source_language", "target_language") if key in args}
            payload = ProjectCreate(name=args.get("project_name") or "Comic", **values)
            project = Project(**payload.model_dump())
            db.add(project)
            db.flush()
        job = FileJob(project_id=project.id, kind="import", filename=project.name,
            snapshot={"image_assets": manifest}, result={"import_operation_id": args["operation_id"], "import_manifest_hash": digest,
                "source_fingerprint": args.get("source_fingerprint"), "submitted_via": "mcp"})
        db.add(job)
        db.commit()
        return {"project_id": project.id, "project_name": project.name, "job": job_read(job), "source_fingerprint": args.get("source_fingerprint")}


def import_project(args):
    """One import surface for images and comic containers, never server paths."""
    from app.services.mcp_files import require_asset
    if bool(args.get("images")) == bool(args.get("asset_id")):
        raise HTTPException(422, "Supply either images or one asset_id")
    if args.get("images"):
        return submit(args)
    asset = require_asset(args["asset_id"])
    filename = PurePosixPath(asset.filename.replace("\\", "/")).name
    suffix = PurePosixPath(filename).suffix.lower()
    if suffix in IMAGE_EXTENSIONS:
        options = {key: value for key, value in args.items() if key != "asset_id"}
        if not options.get("project_id"):
            options.setdefault("project_name", PurePosixPath(filename).stem[:200] or "Comic")
        return submit({**options, "images": [{"asset_id": asset.id, "relative_path": filename}]})
    if suffix not in {".zip", ".cbz", ".rar", ".cbr", ".pdf"}:
        raise HTTPException(422, "Upload images, ZIP/CBZ/RAR/CBR or PDF; source-project archives belong in the web import")
    digest = sha256(json.dumps(args, sort_keys=True).encode()).hexdigest()
    from datetime import timedelta
    from shutil import copyfile

    from app.models.entities import utcnow
    from app.services.file_jobs import job_directory
    with SessionLocal() as db:
        previous = lookup(db, args["operation_id"])
        if previous:
            if previous.result.get("import_manifest_hash") != digest:
                raise HTTPException(409, "Import operation already used for different input")
            return {"project_id": previous.project_id, "job": job_read(previous), "source_fingerprint": previous.result.get("source_fingerprint")}
        pinned = db.scalar(select(MCPAsset).where(MCPAsset.id == asset.id).with_for_update())
        if (not pinned or pinned.status != "AVAILABLE" or pinned.created_at < utcnow()-timedelta(hours=24)
                or not asset_path(asset.id).is_file()):
            raise HTTPException(410, "Upload expired or incomplete")
        if pinned.size > get_settings().max_container_upload_mb * 1024**2:
            raise HTTPException(413, "Comic container exceeds configured byte limit")
        if args.get("project_id"):
            project = db.get(Project, args["project_id"])
            if not project:
                raise HTTPException(404, "Project not found")
            if any(args.get(key) is not None for key in ("project_name", "source_language", "target_language")):
                raise HTTPException(422, "Existing projects retain their name and languages")
        else:
            payload = ProjectCreate(name=args.get("project_name") or PurePosixPath(filename).stem[:200] or "Comic",
                **{key: args[key] for key in ("source_language", "target_language") if key in args})
            project = Project(**payload.model_dump())
            db.add(project)
            db.flush()
        from uuid import uuid4
        identifier = str(uuid4())
        directory = job_directory(identifier)
        directory.mkdir(parents=True)
        copyfile(asset_path(asset.id), directory / ("input" + suffix))
        job = FileJob(id=identifier, project_id=project.id, kind="import", filename=filename,
            result={"import_operation_id": args["operation_id"], "import_manifest_hash": digest,
                "source_fingerprint": args.get("source_fingerprint"), "submitted_via": "mcp"})
        db.add(job)
        db.commit()
        return {"project_id": project.id, "project_name": project.name, "job": job_read(job), "source_fingerprint": args.get("source_fingerprint")}


def prepared_images(manifest, check, progress):
    """Validate every staged image before the worker publishes any page."""
    from app.services.file_import import validate_image
    settings = get_settings()
    total = 0
    result = []
    if not 0 < len(manifest) <= settings.max_import_pages:
        raise ValueError("Directory page count exceeds limit")
    for index, item in enumerate(manifest):
        check()
        path = asset_path(item["asset_id"])
        size = path.stat().st_size
        total += size
        if size > settings.max_upload_mb * 1024**2 or total > settings.max_import_uncompressed_mb * 1024**2:
            raise ValueError("Directory image byte limit exceeded")
        validate_image(path, settings.max_import_page_pixels)
        result.append((path, PurePosixPath(item["relative_path"]).name))
        progress((index+1)/len(manifest))
    return result


def protected_assets(db):
    return {item["asset_id"] for snapshot in db.scalars(select(FileJob.snapshot).where(FileJob.status.not_in(TERMINAL)))
            for item in (snapshot or {}).get("image_assets", [])}

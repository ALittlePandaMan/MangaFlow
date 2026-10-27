"""Durable, single-worker file jobs, independent of page processing admission."""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.transactions import project_write
from app.models import FileJob, ImagePage, Project, TextRegion
from app.services.editor_history import PAGE_FIELDS, _archive_paths
from app.services.file_import import extract_comic
from app.services.page_edits import prepare_project_edit, publish_project_edit
from app.services.workflow_state import page_version, record
from app.storage import get_storage
from fastapi import HTTPException
from sqlalchemy import func, select, update

logger = logging.getLogger(__name__)
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}


class FileJobCancelled(Exception):
    pass


def job_directory(identifier):
    return get_storage().root / "file-jobs" / identifier


def job_read(job):
    return {"id": job.id, "project_id": job.project_id, "kind": job.kind, "status": job.status,
            "source": "mcp" if job.result.get("submitted_via") == "mcp" or job.result.get("import_operation_id") or job.result.get("workflow_run_id") else "web",
            "progress": job.progress, "filename": job.filename, "message": job.message,
            "result": {k: v for k, v in job.result.items() if k != "path"},
            "created_at": job.created_at.isoformat(),
            "download_url": f"/api/file-jobs/{job.id}/download" if job.status == "COMPLETED" and job.result.get("path") else None}


class FileJobWorker:
    def __init__(self):
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        with SessionLocal() as db:
            interrupted = list(db.scalars(select(FileJob.id).where(FileJob.status.in_(["RUNNING", "CANCELLING"]))))
            for identifier in interrupted:
                self.cleanup_import(identifier)
            db.execute(update(FileJob).where(FileJob.status.in_(["RUNNING", "CANCELLING"])).values(
                status="FAILED", message="服务重启中断了文件任务，请重新提交"))
            db.commit()
        self.stop.clear()
        self.thread = threading.Thread(target=self.run, daemon=True, name="mangaflow-files")
        self.thread.start()

    def shutdown(self):
        self.stop.set()
        if self.thread:
            self.thread.join()

    def check(self, identifier):
        with SessionLocal() as db:
            status = db.scalar(select(FileJob.status).where(FileJob.id == identifier))
            if self.stop.is_set() or status not in {"RUNNING", "QUEUED"}:
                raise FileJobCancelled()

    def progress(self, identifier, value, message="", detail=None):
        self.check(identifier)
        with SessionLocal() as db:
            values = {"progress": func.greatest(FileJob.progress, max(0, min(.99, value))), "message": message}
            if detail is not None:
                previous = db.scalar(select(FileJob.result).where(FileJob.id == identifier)) or {}
                values["result"] = {**previous, "progress": detail}
            db.execute(update(FileJob).where(FileJob.id == identifier, FileJob.status == "RUNNING").values(**values))
            db.commit()

    def run(self):
        while not self.stop.is_set():
            try:
                self.collect()
                with SessionLocal() as db:
                    # Row claiming also prevents two workers from taking the same job.
                    job = db.scalar(select(FileJob).where(FileJob.status == "QUEUED")
                                    .order_by(FileJob.created_at, FileJob.id).with_for_update(skip_locked=True).limit(1))
                    if job:
                        identifier, project_id, kind, filename = job.id, job.project_id, job.kind, job.filename
                        job.status = "RUNNING"
                        db.commit()
                if not job:
                    self.stop.wait(.5)
                    continue
                try:
                    if kind == "import":
                        self.import_file(identifier, project_id, filename)
                    else:
                        self.export_file(identifier, project_id, kind)
                except FileJobCancelled:
                    self.cleanup_import(identifier)
                    self.finish(identifier, "CANCELLED", "")
                except Exception as exc:
                    logger.exception("File job %s failed", identifier)
                    self.cleanup_import(identifier)
                    self.finish(identifier, "FAILED", str(getattr(exc, "detail", exc)))
            except Exception:
                logger.exception("File queue unavailable")
                self.stop.wait(2)

    def finish(self, identifier, status, message, result=None, *, db=None):
        def save(writer):
            job = writer.get(FileJob, identifier, populate_existing=True)
            if job:
                if job.status == "COMPLETED" and status != "COMPLETED":
                    return
                job.status, job.message = status, message
                job.progress = 1 if status == "COMPLETED" else job.progress
                delivery = {key: value for key, value in (job.result or {}).items()
                            if key in {"workflow_delivery_version", "mcp_operation_id", "import_operation_id", "import_manifest_hash", "source_fingerprint", "submitted_via"}}
                job.result, job.snapshot = {**delivery, **(result or {})}, {}
        if db is not None:
            save(db)
        else:
            with SessionLocal() as writer:
                save(writer)
                writer.commit()

    def import_file(self, identifier, project_id, filename):
        directory, settings = job_directory(identifier), get_settings()
        source = directory / ("input" + Path(filename).suffix.lower())
        output = directory / "pages"
        with SessionLocal() as db:
            manifest = (db.get(FileJob, identifier).snapshot or {}).get("image_assets")
        if manifest is not None:
            from app.services.mcp_directory_import import prepared_images
            pages = prepared_images(manifest, lambda: self.check(identifier),
                                    lambda value: self.progress(identifier, value * .8))
        elif source.suffix == ".pdf":
            log = directory / "pdf.log"
            with log.open("wb") as stdout:
                # The PDF-only worker has no app imports. Use its absolute entry
                # point so --app-dir/test launches also work outside backend/.
                process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("file_import.py")), str(source), str(output),
                    str(settings.max_import_pages), str(settings.max_import_page_pixels),
                    str(settings.max_import_uncompressed_mb * 1024**2)], stdout=stdout, stderr=subprocess.STDOUT)
                try:
                    while process.poll() is None:
                        self.check(identifier)
                        for line in log.read_text(encoding="utf-8", errors="replace").splitlines()[-1:]:
                            try:
                                value = json.loads(line).get("progress", 0)
                                self.progress(identifier, value * .8)
                            except (ValueError, TypeError):
                                pass
                        self.stop.wait(.25)
                    if process.returncode:
                        message = "无法读取 PDF，文件可能已加密或损坏"
                        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
                            try:
                                message = json.loads(line).get("error", message)
                            except (ValueError, AttributeError):
                                pass
                        raise ValueError(message)
                finally:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
            pages = [(output / path, name) for path, name in json.loads((output / "pages.json").read_text())]
        else:
            pages = extract_comic(source, output, settings, lambda: self.check(identifier),
                                  lambda value: self.progress(identifier, value * .8))
        storage = get_storage()
        rows = []
        for path, name in pages:
            self.check(identifier)
            page_id = str(uuid4())
            with SessionLocal() as db:
                job = db.get(FileJob, identifier)
                if not job:
                    raise FileJobCancelled()
                job.snapshot = {**job.snapshot, "staged_page_ids": [*[row["id"] for row in rows], page_id]}
                db.commit()
            with path.open("rb") as stream:
                relative, width, height = storage.save_upload(project_id, page_id, name, stream)
            rows.append(dict(id=page_id, project_id=project_id, filename=name, original_path=relative,
                width=width, height=height, order_index=0, clean_path=None, rendered_path=None,
                text_layer_path=None, status="UPLOADED", error_message=None, metadata_json={}))
        # All I/O is prepared outside replayable DB writes. Retry a list conflict
        # against the latest order instead of replacing another user's edits.
        while True:
            self.check(identifier)
            try:
                with SessionLocal() as db:
                    prepared = prepare_project_edit(db, project_id, [])
                    before = prepared["before"]
                    next_order = max((item["order_index"] for item in before["order"]), default=-1) + 1
                    ids = [row["id"] for row in rows]
                    for index, row in enumerate(rows):
                        row["order_index"] = next_order + index
                    prepared["before"] = {**before, "tracked_page_ids": ids}
                    after = {"pages": [{"page": {key: row[key] for key in PAGE_FIELDS}, "regions": []} for row in rows],
                             "tracked_page_ids": ids, "order": before["order"] + [
                                 {"id": row["id"], "order_index": row["order_index"]} for row in rows]}
                    def publish(writer, ids=ids):
                        job = writer.get(FileJob, identifier, populate_existing=True, with_for_update=True)
                        if not job or job.status != "RUNNING":
                            raise FileJobCancelled()
                        writer.add_all(ImagePage(**row) for row in rows)
                        self.finish(identifier, "COMPLETED", "", {"page_ids": ids, "page_count": len(ids)}, db=writer)
                    publish_project_edit(db, prepared, after, publish, "导入漫画", ids[0])
                return
            except HTTPException as exc:
                if exc.status_code != 409:
                    raise
                self.progress(identifier, .95, "等待图片列表可编辑")
                self.stop.wait(.5)

    def cleanup_import(self, identifier):
        # A lost commit acknowledgement must not remove published originals.
        with SessionLocal() as db:
            job = db.get(FileJob, identifier)
            if not job or job.kind != "import":
                return
            ids = job.snapshot.get("staged_page_ids", [])
            published = set(db.scalars(select(ImagePage.id).where(ImagePage.id.in_(ids))))
            for page_id in ids:
                if page_id not in published:
                    shutil.rmtree(get_storage().project_dir(job.project_id) / "pages" / page_id, ignore_errors=True)

    def snapshot(self, identifier, project_id, kind):
        # Pin raw paths before copying, then fence both metadata and order. The
        # collector includes these durable references during snapshot preparation.
        storage = get_storage()
        with SessionLocal() as db:
            job = db.get(FileJob, identifier)
            contract = (job.snapshot or {}).get("workflow_contract")
            if contract and not contract.get("delivery_version"):
                # Jobs queued by the previous release kept the same fence in result.
                contract = {**contract, "delivery_version": job.result.get("workflow_delivery_version")}
        def capture(db):
            project = db.get(Project, project_id, populate_existing=True)
            if not project:
                raise ValueError("Project not found")
            pages = list(db.scalars(select(ImagePage).where(ImagePage.project_id == project_id)
                                   .order_by(ImagePage.order_index, ImagePage.id).execution_options(populate_existing=True)))
            regions = list(db.scalars(select(TextRegion).join(ImagePage).where(ImagePage.project_id == project_id)
                                     .execution_options(populate_existing=True)))
            grouped = {page.id: {} for page in pages}
            for region in regions:
                grouped[region.image_id][region.id] = record(region)
            if contract:
                ordered = {page.id: page for page in pages}
                if not set(contract["page_ids"]) <= set(ordered):
                    raise ValueError("Workflow page was deleted; restore it or start a new reviewed scope")
                pages = [ordered[identifier] for identifier in contract["page_ids"]]
            raw = {"project": record(project), "pages": [{"page": record(page), "regions": grouped[page.id]} for page in pages]}
            if contract:
                from app.services.mcp_workflows import validate_export_snapshot
                validate_export_snapshot(raw, contract, db)
            return raw
        self.progress(identifier, .01, detail={"stage": "snapshot"})
        for _attempt in range(5):
            self.check(identifier)
            with SessionLocal() as db:
                def pin():
                    db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
                    raw = capture(db)
                    if not raw["pages"]:
                        raise ValueError("项目中没有页面")
                    job = db.get(FileJob, identifier, populate_existing=True)
                    if not job or job.status != "RUNNING":
                        raise FileJobCancelled()
                    job.snapshot = {**raw, **({"workflow_contract": contract} if contract else {})}
                    return raw
                raw = project_write(db, project_id, pin)
            from app.services.file_export import current_render
            directory, assets = storage.project_dir(project_id), job_directory(identifier) / "snapshot"
            frozen = {"project": _archive_paths(raw["project"], directory, assets) if kind == "project" else deepcopy(raw["project"]), "pages": []}
            if contract:
                frozen["workflow_contract"] = contract
            for index, state in enumerate(raw["pages"]):
                self.check(identifier)
                reusable = kind in {"translated", "cbz", "text_layer"} and current_render(state)
                if kind == "text_layer":
                    reusable = reusable and bool(state["page"].get("text_layer_path")) and storage.absolute(state["page"]["text_layer_path"]).is_file()
                if kind in {"translated", "cbz", "clean", "text_layer"}:
                    # Image-only exports do not consume masks, OCR crops or
                    # history caches. Pin just the pixels/fonts they really use.
                    archived = deepcopy(state)
                    field = ("text_layer_path" if kind == "text_layer" else "rendered_path") if reusable else "clean_path" if state["page"].get("clean_path") else "original_path"
                    archived["page"][field] = _archive_paths(state["page"][field], directory, assets)
                    if not reusable and kind != "clean":
                        for region in archived["regions"].values():
                            data = region.get("layout_data") or {}
                            if data.get("custom_font_path"):
                                data["custom_font_path"] = _archive_paths(data["custom_font_path"], directory, assets)
                else:
                    archived = _archive_paths(state, directory, assets)
                archived["_reuse_render"] = reusable
                frozen["pages"].append(archived)
                if contract and not reusable and kind in {"translated", "cbz", "text_layer", "psd"}:
                    # PSD recreates native layers; passed pages are pixel-compared
                    # during serialization. Other fallback pixels are unreviewed.
                    report_page = next(item for item in contract["report"]["pages"] if item["image_id"] == state["page"]["id"])
                    if report_page["review"]["outcome"] != "passed":
                        report_page["export_rerendered"] = True
                        report_page["review"] = {**report_page["review"], "outcome": "unverified",
                            "note": report_page["review"]["note"] + " Export generated replacement pixels; those pixels were not visually reviewed."}
                        contract["report"]["fully_verified"] = False
                self.progress(identifier, .02 + .10 * (index + 1) / len(raw["pages"]),
                              detail={"stage": "snapshot", "completed": index + 1, "total": len(raw["pages"])})
            with SessionLocal() as db:
                def publish(raw=raw, frozen=frozen):
                    db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
                    actual = capture(db)
                    if ([page_version(state) for state in actual["pages"]] !=
                            [page_version(state) for state in raw["pages"]] or actual["project"] != raw["project"]):
                        return False
                    job = db.get(FileJob, identifier, populate_existing=True)
                    if not job or job.status != "RUNNING":
                        raise FileJobCancelled()
                    job.snapshot = frozen
                    return True
                if project_write(db, project_id, publish):
                    if kind in {"translated", "cbz", "psd", "text_layer"}:
                        self.freeze_fonts(identifier, frozen, kind)
                    return frozen
        raise ValueError("页面在生成快照时持续变化，请稍后重新导出")

    def freeze_fonts(self, identifier, snapshot, kind):
        from app.services.layout import FontResolver
        storage = get_storage()
        states = [state for state in snapshot["pages"] if kind == "psd" or not state.get("_reuse_render")]
        regions = [region for state in states for region in state["regions"].values()
                   if region["translated_text"].strip() and (kind == "psd" or region["visible"])]
        if not regions:
            return
        self.progress(identifier, .12, detail={"stage": "fonts"})
        resolver = FontResolver([storage.root / "fonts"], project_root=storage.root / "projects")
        copied = {}
        def copy(source):
            source = Path(source).resolve()
            if source not in copied:
                self.check(identifier)
                target = job_directory(identifier) / "fonts" / f"{len(copied):04d}-{source.name}"
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(source, target)
                copied[source] = str(target)
            return copied[source]
        for index, region in enumerate(regions):
            data = region.get("layout_data") or {}
            custom = data.get("custom_font_path")
            # Archived custom fonts are storage-relative; the renderer accepts
            # absolute custom paths, so resolve only inside this private copy.
            if custom and not Path(custom).is_absolute():
                custom = str(storage.absolute(custom))
            resolve = resolver.prepare(region["font_family"], custom, region["font_weight"])
            copy(resolve(None))
            for character in set(region["translated_text"]):
                copy(resolve(character))
            if custom:
                region["layout_data"] = {**data, "custom_font_path": copy(resolve(None))}
            self.progress(identifier, .12 + .04 * (index + 1) / len(regions),
                          detail={"stage": "fonts", "completed": index + 1, "total": len(regions)})
        snapshot["_font_paths"] = list(copied.values())

    def export_file(self, identifier, project_id, kind):
        from app.services.file_export import export_snapshot
        snapshot = self.snapshot(identifier, project_id, kind)
        last_value, last_stage, last_time = 0, None, 0
        def report(value, stage, completed=0, total=0):
            nonlocal last_value, last_stage, last_time
            value = .16 + .83 * value
            now = time.monotonic()
            # Frequent region/chunk callbacks remain cheap and cannot flood DB
            # writes; stage changes and meaningful percentage changes flush now.
            if stage != last_stage or value - last_value >= .01 or now - last_time >= .2:
                self.progress(identifier, value, detail={"stage": stage, "completed": completed, "total": total})
                last_value, last_stage, last_time = value, stage, now
        path, result = export_snapshot(snapshot, kind, job_directory(identifier),
            lambda: self.check(identifier), report)
        contract = snapshot.get("workflow_contract")
        if contract:
            from app.services.mcp_workflows import write_export_reports
            result["workflow_run_id"] = contract["run_id"]
            result["report_downloads"] = write_export_reports(identifier, contract, result)
        self.check(identifier)
        with SessionLocal() as db:
            job = db.get(FileJob, identifier, with_for_update=True)
            if not job or job.status != "RUNNING":
                raise FileJobCancelled()
            self.finish(identifier, "COMPLETED", "", {**result, "path": path.name}, db=db)
            db.commit()

    def collect(self):
        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        with SessionLocal() as db:
            jobs = db.scalars(select(FileJob).where(FileJob.status.in_(TERMINAL), FileJob.updated_at < cutoff)).all()
            for job in jobs:
                shutil.rmtree(job_directory(job.id), ignore_errors=True)
                db.delete(job)
            db.commit()
            known = set(db.scalars(select(FileJob.id)))
        root = get_storage().root / "file-jobs"
        if root.exists():
            for directory in root.iterdir():
                if directory.is_dir() and not directory.is_symlink() and directory.name not in known and directory.stat().st_mtime < cutoff.timestamp():
                    shutil.rmtree(directory)


file_worker = FileJobWorker()

"""Idle maintenance for unreferenced history assets, with a ten-minute grace."""
from __future__ import annotations

import logging
import time

from app.core.config import get_settings
from app.models import (
    EditorHistoryEntry,
    ImagePage,
    ProcessingTask,
    Project,
    RegionRevision,
    StageCheckpoint,
    TaskWorkflow,
    TextRegion,
)
from app.services.editor_history import PAGE_FIELDS, ensure_project_idle
from app.services.regions import region_snapshot
from app.storage import get_storage
from sqlalchemy import select, update
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def collect_project_history_assets(db: Session, project_id: str, *, grace_seconds: float = 600, max_files: int = 128) -> dict:
    storage = get_storage()
    directory = storage.project_dir(project_id) / "history" / "assets"
    # A write reservation makes the mark/sweep atomic with history commands.
    # This never runs in a region edit request; deletion is bounded per pass.
    db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
    ensure_project_idle(db, project_id)
    prefix = storage.relative(directory) + "/"
    references: set[str] = set()

    def mark(value):
        if isinstance(value, str) and value.startswith(prefix):
            references.add(value)
        elif isinstance(value, dict):
            for item in value.values():
                mark(item)
        elif isinstance(value, list):
            for item in value:
                mark(item)

    for before, after in db.execute(select(EditorHistoryEntry.before, EditorHistoryEntry.after).where(EditorHistoryEntry.project_id == project_id)):
        mark(before)
        mark(after)
    for page in db.scalars(select(ImagePage).where(ImagePage.project_id == project_id)):
        mark({field: getattr(page, field) for field in PAGE_FIELDS})
    for region in db.scalars(select(TextRegion).join(ImagePage).where(ImagePage.project_id == project_id)):
        mark(region_snapshot(region))
    # Region revisions are independently restorable, so retain their assets too.
    for snapshot in db.scalars(select(RegionRevision.snapshot).join(TextRegion).join(ImagePage).where(ImagePage.project_id == project_id)):
        mark(snapshot)
    # Failed/paused workflows may resume from old archived masks even after
    # editor history was trimmed. Their durable input/result references count
    # as live until the checkpoint retention policy explicitly releases them.
    for state in db.scalars(select(TaskWorkflow.input_state).join(ProcessingTask).where(
        ProcessingTask.project_id == project_id, TaskWorkflow.status != "PUBLISHED",
    )):
        mark(state)
    for result in db.scalars(select(StageCheckpoint.result).join(TaskWorkflow).join(ProcessingTask).where(
        ProcessingTask.project_id == project_id, TaskWorkflow.status != "PUBLISHED", StageCheckpoint.status == "SUCCEEDED",
    )):
        mark(result)
    project = db.get(Project, project_id)
    if project:
        mark(project.settings)
    result = {"removed_files": 0, "removed_bytes": 0}
    cutoff = time.time() - max(0, grace_seconds)
    if directory.is_dir() and not directory.is_symlink():
        for path in directory.iterdir():
            if path.is_symlink() or not path.is_file() or path.resolve().parent != directory.resolve():
                continue
            stat = path.stat()
            if stat.st_mtime > cutoff or storage.relative(path) in references:
                continue
            path.unlink()
            result["removed_files"] += 1
            result["removed_bytes"] += stat.st_size
            if result["removed_files"] >= max_files:
                break
    if result["removed_files"]:
        # Cached archive paths must not point to a collected file on reuse.
        from app.services.editor_history import _archive_file
        _archive_file.cache_clear()
    # Clear the stat-keyed archive cache while the write reservation still
    # excludes a new edit from reusing a just-collected cached pathname.
    db.commit()
    return result


def collect_idle_history_assets() -> None:
    if get_settings().maintenance_mode:
        return
    from app.core.database import SessionLocal
    with SessionLocal() as db:
        projects = list(db.scalars(select(Project.id)))
    for project_id in projects:
        directory = get_storage().project_dir(project_id) / "history" / "assets"
        if not directory.is_dir():
            continue
        with SessionLocal() as db:
            try:
                collect_project_history_assets(db, project_id)
            except Exception:
                # Busy projects are expected; never interrupt processing.
                db.rollback()
                logger.debug("History maintenance deferred for project %s", project_id, exc_info=True)

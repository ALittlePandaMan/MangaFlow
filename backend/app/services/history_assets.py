"""Idle maintenance for unreferenced history assets, with a ten-minute grace."""
from __future__ import annotations

import logging
import time

from app.core.config import get_settings
from app.core.transactions import has_pending_writes
from app.models import (
    EditorHistoryEntry,
    FileJob,
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
from sqlalchemy import select, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _history_references(db: Session, project_id: str, prefix: str) -> set[str]:
    references: set[str] = set()

    def mark(value):
        if isinstance(value, str) and value.startswith(prefix):
            references.add(value)
        elif isinstance(value, dict):
            for key, item in value.items():
                mark(key)  # Checkpoint manifests can use file paths as keys.
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
    for snapshot in db.scalars(select(FileJob.snapshot).where(FileJob.project_id == project_id,
                                                             FileJob.status.in_(["RUNNING", "CANCELLING"]))):
        mark(snapshot)
    if project:
        mark(project.settings)
    return references


def collect_project_history_assets(db: Session, project_id: str, *, grace_seconds: float = 600,
                                   max_files: int = 128, time_budget: float = .1) -> dict:
    """Prepare scans without row locks; fence only a small deletion window.

    Every history publisher updates the project row, including no-op updates.
    Its PostgreSQL xmin therefore detects intervening publications without
    fetching large JSON snapshots while holding the shared project lock.
    Maintenance never waits for user work and never changes project timestamps.
    """
    if has_pending_writes(db):
        raise ValueError("History collection requires a clean session")
    db.rollback()
    storage = get_storage()
    directory = storage.project_dir(project_id) / "history" / "assets"
    result = {"removed_files": 0, "removed_bytes": 0}
    if max_files < 1 or not directory.is_dir() or directory.is_symlink():
        return result
    cutoff = time.time() - max(0, grace_seconds)
    candidates = []
    for path in directory.iterdir():
        if path.is_symlink() or path.resolve().parent != directory or not path.is_file():
            continue
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        if stat.st_mtime <= cutoff:
            candidates.append((path, stat.st_ino, stat.st_size, stat.st_mtime_ns))
    if not candidates:
        return result
    try:
        version = db.scalar(text("SELECT xmin::text FROM projects WHERE id = :id"), {"id": project_id})
        if version is None:
            return result
        ensure_project_idle(db, project_id)
        references = _history_references(db, project_id, storage.relative(directory) + "/")
        db.rollback()  # No read transaction/connection survives the preparation.
        # A large live history must not spend the deletion window just walking
        # referenced entries, starving the orphan files at the end forever.
        candidates = [item for item in candidates if storage.relative(item[0]) not in references]
        if not candidates:
            return result
        # Skip a busy project immediately. The timeout also bounds idle checks;
        # filesystem enumeration and large reference reads happened above.
        db.execute(text("SET LOCAL statement_timeout = '500ms'"))
        current = db.scalar(text("SELECT xmin::text FROM projects WHERE id = :id FOR UPDATE NOWAIT"), {"id": project_id})
        if current != version:
            return result
        ensure_project_idle(db, project_id)
        deadline = time.monotonic() + max(.001, min(time_budget, .25))
        for path, inode, size, modified in candidates:
            if time.monotonic() > deadline or result["removed_files"] >= max_files:
                break
            if path.is_symlink() or path.resolve().parent != directory:
                continue
            try:
                stat = path.stat()
                if (stat.st_ino, stat.st_size, stat.st_mtime_ns) != (inode, size, modified):
                    continue
                path.unlink()
            except FileNotFoundError:
                continue
            result["removed_files"] += 1
            result["removed_bytes"] += size
        return result
    finally:
        if result["removed_files"]:
            # Invalidate while publishers are still excluded by our row lock.
            from app.services.editor_history import _archive_file
            _archive_file.cache_clear()
        db.rollback()


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

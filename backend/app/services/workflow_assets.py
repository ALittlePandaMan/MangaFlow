"""Bounded idle collection of generated, unreferenced workflow image files.

Successful unpublished results and uncertain cloud attempts remain resumable.
Metadata is retained with its task; current state, revisions and both directions
of editor history always take precedence over the filesystem grace period.
"""
from __future__ import annotations

import logging
import re
import threading
import time

from app.core.config import get_settings
from app.core.database import SessionLocal
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
from app.services.editor_history import ensure_project_idle
from app.storage import get_storage
from sqlalchemy import String, cast, or_, select, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
GENERATED = {"original","masks","bubbles","clean","rendered","layers","versions"}
IMAGES = {".png",".jpg",".jpeg",".webp"}
_scan_cursors: dict[str,str] = {}
_scan_lock = threading.Lock()


class CollectionDeferred(RuntimeError):
    pass


def _candidates(storage, project_id, cutoff, scan_limit):
    project = storage.root / "projects" / project_id
    if project.resolve() != project or not (project / "pages").is_dir():
        return []
    found,scanned = [],0
    key = str(project)
    with _scan_lock:
        cursor = _scan_cursors.get(key,"")
    last = cursor
    for page in sorted((project / "pages").iterdir()):
        if page.is_symlink():
            continue
        # Fixed depth also keeps symlinked directories out of traversal.
        for path in _generated_page_files(page):
            relative = path.relative_to(project).as_posix()
            if relative <= cursor:
                continue
            scanned += 1
            if scanned > scan_limit:
                with _scan_lock:
                    _scan_cursors[key] = last
                return found
            last = relative
            # Exact page/workflow/checkpoint/output/file layout, never a
            # recursive deletion or an arbitrary path from persisted JSON.
            parts = path.relative_to(page).parts
            workflow = len(parts)==5 and parts[0]=="workflows" and parts[3] in GENERATED
            exported = len(parts)==2 and parts[0] in {"rendered","layers"} and re.fullmatch(r"export-[0-9a-f]{32}\.png",parts[1])
            if not (workflow or exported) or path.suffix.lower() not in IMAGES:
                continue
            if path.is_symlink() or path.resolve() != path or not path.is_file():
                continue
            stat = path.stat()
            if stat.st_mtime <= cutoff:
                found.append((path,stat.st_ino,stat.st_size,stat.st_mtime_ns))
    with _scan_lock:
        _scan_cursors.pop(key,None)
    return found


def _generated_page_files(page):
    # Lexical order keeps the bounded scan cursor valid across both namespaces.
    for name in ("layers","rendered"):
        directory = page/name
        if directory.is_dir() and not directory.is_symlink():
            yield from sorted(directory.glob("export-*.png"))
    workflows = page/"workflows"
    if not workflows.is_dir() or workflows.is_symlink() or workflows.resolve()!=workflows:
        return
    yield from _workflow_files(workflows)


def _workflow_files(workflows):
    for workflow in sorted(workflows.iterdir()):
        if not workflow.is_dir() or workflow.is_symlink():
            continue
        for checkpoint in sorted(workflow.iterdir()):
            if not checkpoint.is_dir() or checkpoint.is_symlink():
                continue
            for category in sorted(checkpoint.iterdir()):
                if category.name in GENERATED and category.is_dir() and not category.is_symlink():
                    yield from sorted(category.iterdir())


def _references(db, project_id, prefix, deadline):
    references, protected = set(), set()

    def check():
        if time.monotonic() > deadline:
            raise CollectionDeferred("Workflow collection time budget exceeded")

    def mark(value):
        check()
        if isinstance(value,str) and value.startswith(prefix):
            references.add(value)
        elif isinstance(value,dict):
            for key,item in value.items():
                # Artifact/input manifests use paths as keys.
                mark(key)
                mark(item)
        elif isinstance(value,list):
            for item in value:
                mark(item)

    def has_path(column):
        return cast(column,String).contains(prefix,autoescape=True)

    queries = [
        select(Project.settings).where(Project.id == project_id),
        select(ImagePage.original_path,ImagePage.clean_path,ImagePage.rendered_path,ImagePage.text_layer_path,ImagePage.metadata_json).where(ImagePage.project_id == project_id),
        select(TextRegion.pixel_mask_path,TextRegion.inpainted_path,TextRegion.layout_data).join(ImagePage).where(ImagePage.project_id == project_id),
        select(EditorHistoryEntry.before,EditorHistoryEntry.after).where(EditorHistoryEntry.project_id == project_id,
            or_(has_path(EditorHistoryEntry.before),has_path(EditorHistoryEntry.after))),
        select(RegionRevision.snapshot).join(TextRegion).join(ImagePage).where(ImagePage.project_id == project_id,has_path(RegionRevision.snapshot)),
        select(TaskWorkflow.input_state,TaskWorkflow.policy).join(ProcessingTask).where(
            ProcessingTask.project_id == project_id,TaskWorkflow.status != "PUBLISHED"),
        select(StageCheckpoint.result,StageCheckpoint.artifacts).join(TaskWorkflow).join(ProcessingTask).where(
            ProcessingTask.project_id == project_id,TaskWorkflow.status != "PUBLISHED",StageCheckpoint.status == "SUCCEEDED"),
    ]
    for query in queries:
        for row in db.execute(query.execution_options(yield_per=64)):
            for value in row:
                mark(value)
    for page_id,workflow_id,checkpoint_id in db.execute(select(ProcessingTask.image_id,TaskWorkflow.id,StageCheckpoint.id)
        .join(TaskWorkflow,TaskWorkflow.task_id == ProcessingTask.id)
        .join(StageCheckpoint,StageCheckpoint.workflow_id == TaskWorkflow.id)
        .where(ProcessingTask.project_id == project_id,StageCheckpoint.status.in_(["RUNNING","UNKNOWN"]))):
        protected.add(f"{prefix}{page_id}/workflows/{workflow_id}/{checkpoint_id}/")
    check()
    return references,protected


def collect_project_workflow_assets(project_id: str, *, sessions=SessionLocal, storage=None,
    grace_seconds: float = 7*24*3600, max_files: int = 128, scan_limit: int = 8192, time_budget: float = 1) -> dict:
    """Skip busy projects; a short, non-waiting maintenance transaction fences edits."""
    storage = storage or get_storage()
    result = {"removed_files":0,"removed_bytes":0,"deferred":False}
    candidates = _candidates(storage,project_id,time.time()-max(0,grace_seconds),scan_limit)
    if not candidates or max_files < 1:
        return result
    with sessions() as db:
        engine = db.get_bind()
    with engine.connect() as connection:
        try:
            connection.execute(text("SET LOCAL statement_timeout = '1000ms'"))
            with Session(bind=connection) as db:
                project = db.scalar(select(Project.id).where(Project.id == project_id).with_for_update(nowait=True))
                if project is None:
                    return result
                # Region edits take this same page lock. NOWAIT also avoids a
                # lock-order deadlock with a page edit finalizing its journal.
                list(db.scalars(select(ImagePage.id).where(ImagePage.project_id == project_id).with_for_update(nowait=True)))
                ensure_project_idle(db,project_id)
                deadline = time.monotonic()+max(.001,time_budget)
                prefix = f"projects/{project_id}/pages/"
                references,protected = _references(db,project_id,prefix,deadline)
                for path,inode,size,modified in candidates:
                    if time.monotonic() > deadline or result["removed_files"] >= max_files:
                        break
                    relative = path.relative_to(storage.root).as_posix()
                    if relative in references or any(relative.startswith(directory) for directory in protected):
                        continue
                    if path.is_symlink() or path.resolve() != path:
                        continue
                    stat = path.stat()
                    if (stat.st_ino,stat.st_size,stat.st_mtime_ns) != (inode,size,modified):
                        continue
                    path.unlink()
                    result["removed_files"] += 1
                    result["removed_bytes"] += size
            connection.commit()
        except Exception:
            connection.rollback()
            result["deferred"] = True
            logger.debug("Workflow collection deferred for project %s",project_id,exc_info=True)
        finally:
            # SELECT FOR UPDATE does not mutate project/user timestamps.
            connection.rollback()
    return result


def collect_idle_workflow_assets():
    if get_settings().maintenance_mode or not get_settings().checkpoint_gc_enabled:
        return
    with SessionLocal() as db:
        projects = list(db.scalars(select(Project.id)))
    for project_id in projects:
        collect_project_workflow_assets(project_id)

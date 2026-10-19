from __future__ import annotations

import hashlib
import os
import shutil
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from stat import S_ISREG
from uuid import uuid4

from app.core.timing import measured
from app.core.transactions import has_pending_writes, retry_write
from app.models import EditorHistoryEntry, ImagePage, ProcessingTask, Project, TextRegion
from app.services.regions import SNAPSHOT_FIELDS, region_snapshot, save_revision
from app.storage import get_storage
from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

HISTORY_LIMIT = 100
PAGE_FIELDS = (
    "id",
    "project_id",
    "filename",
    "original_path",
    "width",
    "height",
    "order_index",
    "clean_path",
    "rendered_path",
    "text_layer_path",
    "status",
    "error_message",
    "metadata_json",
)
ACTIVE_TASK_STATES = ("QUEUED", "RUNNING", "CANCELLING", "PAUSED")


def ensure_page_idle(db: Session, page: ImagePage) -> None:
    if db.scalar(
        select(ProcessingTask.id)
        .where(
            ProcessingTask.image_id == page.id,
            ProcessingTask.status.in_(ACTIVE_TASK_STATES),
        )
        .limit(1)
    ):
        raise HTTPException(409, "请等待当前页面任务完成或取消后再编辑、撤销或重做")
    if db.scalar(
        select(EditorHistoryEntry.id)
        .where(EditorHistoryEntry.image_id == page.id, EditorHistoryEntry.pending.is_(True))
        .limit(1)
    ):
        raise HTTPException(409, "正在完成当前操作的历史记录，请稍后重试")


@lru_cache(maxsize=2048)
def _archive_file(source: str, size: int, modified: int, directory: str) -> str:
    # The stat signature prevents stale reuse when the pipeline overwrites its
    # usual output path. Content-addressing deduplicates unchanged assets.
    path = Path(source)
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    target = Path(directory) / f"{digest.hexdigest()}{path.suffix}"
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        temporary = target.with_name(f".{uuid4().hex}.tmp")
        try:
            shutil.copyfile(path, temporary)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return get_storage().relative(target)


class _SnapshotArchive:
    """Resolve each asset once per snapshot/restore, never across mutations."""

    def __init__(self, directory: Path, assets: Path):
        self.storage = get_storage()
        self.prefix = self.storage.relative(directory) + "/"
        self.assets = assets
        self.paths: dict[str, str] = {}

    def __call__(self, value):
        if isinstance(value, dict):
            return {
                key: deepcopy(item)
                if key in {"source_text", "translated_text", "text", "prompt", "raw_response"}
                else self(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self(item) for item in value]
        if not isinstance(value, str) or not value.startswith(self.prefix):
            return value
        if value in self.paths:
            return self.paths[value]
        path = self.storage.absolute(value)
        archived = value
        if not path.is_relative_to(self.assets):
            try:
                signature = path.stat()
            except (FileNotFoundError, NotADirectoryError):
                signature = None
            if signature is not None and S_ISREG(signature.st_mode):
                archived = _archive_file(str(path), signature.st_size, signature.st_mtime_ns, str(self.assets))
        self.paths[value] = archived
        return archived


@measured("history.archive")
def _archive_paths(value, page_directory: Path, assets: Path):
    return _SnapshotArchive(page_directory, assets)(value)


def _page_state(db: Session, page: ImagePage, *, region_ids: list[str] | None = None) -> dict:
    """DB-only snapshot, kept separate from hashing/copying image assets."""
    db.flush()
    query = select(TextRegion).where(TextRegion.image_id == page.id)
    if region_ids is not None:
        query = query.where(TextRegion.id.in_(region_ids))
    regions = db.scalars(
        query.order_by(TextRegion.reading_order, TextRegion.created_at, TextRegion.id).execution_options(populate_existing=True)
    ).all()
    state = {
        "page": {field: deepcopy(getattr(page, field)) for field in PAGE_FIELDS},
        "regions": [
            {"id": region.id, "region_key": region.region_key, **region_snapshot(region)} for region in regions
        ],
    }
    if region_ids is not None:
        state["_partial_region_ids"] = sorted(set(region_ids))
    return state


class _HistorySnapshotChanged(Exception):
    pass


def _prepare_task_snapshot(db: Session, image_id: str | None) -> tuple[dict | None, dict | None]:
    """The task/pending journal protects assets while no DB writer is held.

    Release even the read transaction before filesystem I/O. The short publish
    transaction verifies the raw snapshot again, so a stale capture is never
    silently committed. Interactive edit transactions retain their old path.
    """
    page = db.get(ImagePage, image_id) if image_id else None
    raw = _page_state(db, page) if page else None
    project_id = page.project_id if page else None
    db.rollback()
    if raw is None:
        return None, None
    directory = get_storage().project_dir(project_id)
    return raw, _archive_paths(raw, directory, directory / "history" / "assets")


def _verify_task_snapshot(db: Session, image_id: str | None, expected: dict | None) -> ImagePage | None:
    page = db.get(ImagePage, image_id) if image_id else None
    actual = _page_state(db, page) if page else None
    if actual != expected:
        raise _HistorySnapshotChanged("Page changed while preparing task history")
    return page


def _invalidate_state_cache(db: Session, project_id: str) -> None:
    db.info.pop(("editor_history", project_id), None)


def history_state(db: Session, image_id: str) -> dict:
    page = db.get(ImagePage, image_id)
    return {**project_history_state(db, page.project_id), "image_id": image_id}


def project_history_state(db: Session, project_id: str) -> dict:
    key = ("editor_history", project_id)
    if key in db.info:
        return db.info[key]
    # Toolbar/region responses need cursors, not megabytes of before/after
    # snapshots. Selecting ORM entities deserialized every checkpoint on every
    # mask stroke and region update.
    entries = list(
        db.execute(
            select(EditorHistoryEntry.id, EditorHistoryEntry.action, EditorHistoryEntry.applied)
            .where(
                EditorHistoryEntry.project_id == project_id,
                EditorHistoryEntry.pending.is_(False),
            )
            .order_by(EditorHistoryEntry.id)
        ).all()
    )
    applied = [entry for entry in entries if entry.applied]
    unapplied = [entry for entry in entries if not entry.applied]
    state = {
        "project_id": project_id,
        "undo_count": len(applied),
        "redo_count": len(unapplied),
        "undo_id": applied[-1].id if applied else None,
        "redo_id": unapplied[0].id if unapplied else None,
        "undo_label": applied[-1].action if applied else None,
        "redo_label": unapplied[0].action if unapplied else None,
        "pending": db.scalar(
            select(EditorHistoryEntry.id)
            .where(EditorHistoryEntry.project_id == project_id, EditorHistoryEntry.pending.is_(True))
            .limit(1)
        )
        is not None
        or db.scalar(
            select(ProcessingTask.id)
            .where(ProcessingTask.project_id == project_id, ProcessingTask.status.in_(ACTIVE_TASK_STATES))
            .limit(1)
        )
        is not None,
    }
    db.info[key] = state
    return state


def _finish_entry(db: Session, entry: EditorHistoryEntry, after: dict) -> None:
    entry.after = after
    entry.pending = False
    db.flush()
    # A no-op or failed command with no page mutation must not consume history
    # or destroy the redo branch.
    if entry.before == after:
        db.delete(entry)
    else:
        db.execute(
            delete(EditorHistoryEntry).where(
                EditorHistoryEntry.project_id == entry.project_id,
                EditorHistoryEntry.applied.is_(False),
                EditorHistoryEntry.id != entry.id,
            )
        )
        older = list(
            db.scalars(
                select(EditorHistoryEntry.id)
                .where(
                    EditorHistoryEntry.project_id == entry.project_id,
                    EditorHistoryEntry.pending.is_(False),
                )
                .order_by(EditorHistoryEntry.id.desc())
                .offset(HISTORY_LIMIT)
            ).all()
        )
        if older:
            db.execute(
                delete(EditorHistoryEntry).where(EditorHistoryEntry.id.in_(older), EditorHistoryEntry.id != entry.id)
            )
    db.flush()
    _invalidate_state_cache(db, entry.project_id)


@measured("history.begin")
def begin_task_history(db: Session, task: ProcessingTask) -> int | None:
    task_id, image_id, project_id = task.id, task.image_id, task.project_id
    if not image_id:
        return None
    if has_pending_writes(db):
        raise ValueError("Task history requires committed page changes")
    for attempt in range(3):
        raw, before = _prepare_task_snapshot(db, image_id)
        try:
            def save(raw=raw, before=before):
                # Lock order stays project -> page/journal. No filesystem I/O
                # may run inside this replayable transaction.
                db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
                current = db.get(ProcessingTask, task_id)
                if current is None or _verify_task_snapshot(db, image_id, raw) is None:
                    return None
                return _begin_task_history(db, current, before)

            result = retry_write(db, save)
            _invalidate_state_cache(db, project_id)
            return result
        except _HistorySnapshotChanged:
            if attempt == 2:
                raise
    return None


def _begin_task_history(db: Session, task: ProcessingTask, before: dict) -> int:
    """DB-only journal mutation; caller owns transaction and snapshot validation."""
    existing = db.scalar(select(EditorHistoryEntry).where(EditorHistoryEntry.task_id == task.id))
    if existing:
        existing.pending = True
        return existing.id
    linked_id = (task.payload or {}).get("options", {}).get("_editor_history_entry_id")
    linked = db.get(EditorHistoryEntry, linked_id) if isinstance(linked_id, int) else None
    if (
        linked
        and linked.image_id == task.image_id
        and linked.applied
        and linked.id == history_state(db, task.image_id)["undo_id"]
    ):
        # A repair/render task may affect more than the preceding mask edit.
        # Expand its sparse baseline before linking, preserving the edited
        # region's original values and all other regions' current values.
        if "_partial_region_ids" in linked.before:
            baseline = deepcopy(before)
            originals = {item["id"]: item for item in linked.before["regions"]}
            baseline["regions"] = [originals.get(item["id"], item) for item in baseline["regions"]]
            baseline["page"] = linked.before["page"]
            linked.before = baseline
        linked.task_id = task.id
        linked.pending = True
        return linked.id
    stage = (task.payload or {}).get("start_stage") or task.task_type
    label = {
        "detection": "检测文字区域",
        "ocr": "OCR 识别",
        "translation": "翻译文字",
        "mask": "生成蒙版与修复",
        "inpainting": "修复背景",
        "rendering": "重新排版",
    }.get(stage, "页面处理")
    region_ids = [task.region_id] if task.region_id else (task.payload or {}).get("options", {}).get("region_ids", [])
    entry = EditorHistoryEntry(
        project_id=task.project_id,
        image_id=task.image_id,
        task_id=task.id,
        action=label,
        before=before,
        after=before,
        region_ids=region_ids,
        pending=True,
    )
    db.add(entry)
    db.flush()
    return entry.id


@measured("history.finish")
def finish_task_history(db: Session, task_id: str, entry_id: int | None) -> None:
    if entry_id is None:
        return
    db.rollback()
    from app.models import TaskWorkflow
    workflow = db.scalar(select(TaskWorkflow).where(TaskWorkflow.task_id == task_id).order_by(
        TaskWorkflow.created_at.desc(),
    ).limit(1))
    entry = db.get(EditorHistoryEntry,entry_id)
    geometry_prepared = bool(entry and (entry.after or {}).get("_prepared_target_geometry"))
    if workflow is not None and workflow.status != "PUBLISHED" and not geometry_prepared:
        # A staged workflow never changed the published page. Do not capture
        # an unrelated edit as this failed/paused task's undo operation.
        entry = db.get(EditorHistoryEntry, entry_id)
        if entry is None:
            return
        project_id = entry.project_id

        def discard_unpublished():
            db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
            current = db.get(EditorHistoryEntry, entry_id)
            task = db.get(ProcessingTask, task_id)
            if current is None or not current.pending or current.task_id != task_id:
                return
            linked = (task.payload or {}).get("options", {}).get("_editor_history_entry_id") if task else None
            if linked == current.id:
                current.pending, current.task_id = False, None
            else:
                db.delete(current)

        retry_write(db, discard_unpublished)
        _invalidate_state_cache(db, project_id)
        return
    for attempt in range(3):
        entry = db.get(EditorHistoryEntry, entry_id)
        if not entry:
            return
        project_id, image_id = entry.project_id, entry.image_id
        raw, after = _prepare_task_snapshot(db, image_id)

        def save(project_id=project_id, image_id=image_id, raw=raw, after=after):
            db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
            current = db.get(EditorHistoryEntry, entry_id)
            if current is None:
                return
            page = _verify_task_snapshot(db, image_id, raw)
            if page:
                _finish_entry(db, current, {**after,"_prepared_target_geometry":True} if geometry_prepared else after)
            else:
                db.delete(current)

        try:
            retry_write(db, save)
            _invalidate_state_cache(db, project_id)
            return
        except _HistorySnapshotChanged:
            if attempt == 2:
                raise


def recover_task_history(db: Session) -> None:
    # A process can stop after a pipeline commit but before its journal closes.
    # Finalize terminal/paused checkpoints on startup, retaining their original
    # before state so a partial failure is still reversible.
    entries = list(db.scalars(select(EditorHistoryEntry).where(EditorHistoryEntry.pending.is_(True))).all())
    for entry in entries:
        task = db.get(ProcessingTask, entry.task_id) if entry.task_id else None
        if task and task.status in {"QUEUED", "RUNNING", "CANCELLING"}:
            continue
        finish_task_history(db, entry.task_id, entry.id)


def _prepare_restore_page(project_id: str, state: dict, current: dict) -> dict:
    """Verify assets and compare archived content with no application writer."""
    project_directory = get_storage().project_dir(project_id)
    assets = project_directory/"history"/"assets"
    directory = get_storage().relative(assets)+"/"
    verified: set[str] = set()

    def verify(value):
        if isinstance(value, dict):
            for item in value.values():
                verify(item)
        elif isinstance(value, list):
            for item in value:
                verify(item)
        elif isinstance(value, str) and value.startswith(directory) and value not in verified:
            if not get_storage().absolute(value).is_file():
                raise HTTPException(409, "历史文件已丢失，无法完整恢复；当前页面未改动")
            verified.add(value)

    # Sparse previews and retry bookkeeping are disposable accelerators, not
    # the authoritative mask/page assets required to restore this command.
    verify({key: value for key, value in state.items() if key not in {"_mask_delta", "_history_commands"}})
    archive = _SnapshotArchive(project_directory, assets)
    snapshots = {identifier:{**row,"archived":archive(row["snapshot"])} for identifier,row in current.items()}
    changed = [item["id"] for item in state["regions"] if item["id"] not in snapshots
        or snapshots[item["id"]]["region_key"]!=item["region_key"]
        or any(snapshots[item["id"]]["archived"][field]!=item[field] for field in SNAPSHOT_FIELDS)]
    return {"changed":changed}


def restore_page_state(db: Session, page: ImagePage, state: dict, prepared: dict) -> None:
    """DB-only replay of an asset-verified restoration plan."""
    query = select(TextRegion).where(TextRegion.image_id == page.id)
    if "_partial_region_ids" in state:
        query = query.where(TextRegion.id.in_(state["_partial_region_ids"]))
    existing = {region.id: region for region in db.scalars(query).all()}
    wanted = {item["id"] for item in state["regions"]}
    changed = [item for item in state["regions"] if item["id"] in prepared["changed"]]
    changed_keys = {
        item["id"]
        for item in changed
        if item["id"] in existing and existing[item["id"]].region_key != item["region_key"]
    }
    for region in existing.values():
        if region.id not in wanted:
            db.delete(region)
        elif region.id in changed_keys:
            region.region_key = f"__history_{uuid4().hex[:12]}"
    db.flush()
    restored = []
    for item in changed:
        region = existing.get(item["id"])
        if region is None:
            region = TextRegion(id=item["id"], image_id=page.id, region_key=item["region_key"])
            db.add(region)
        region.region_key = item["region_key"]
        for field in SNAPSHOT_FIELDS:
            setattr(region, field, deepcopy(item[field]))
        restored.append(region)
    # Batch the ORM writes; a flush per region amplifies large-page restores.
    db.flush()
    for region in restored:
        save_revision(db, region, "editor_history_restored")
    for field in PAGE_FIELDS:
        setattr(page, field, deepcopy(state["page"][field]))
    page.current_stage = None
    db.flush()
    db.expire(page, ["regions"])


def ensure_project_idle(db: Session, project_id: str) -> None:
    if db.scalar(
        select(ProcessingTask.id)
        .where(ProcessingTask.project_id == project_id, ProcessingTask.status.in_(ACTIVE_TASK_STATES))
        .limit(1)
    ) or db.scalar(
        select(EditorHistoryEntry.id)
        .where(EditorHistoryEntry.project_id == project_id, EditorHistoryEntry.pending.is_(True))
        .limit(1)
    ):
        raise HTTPException(409, "请等待项目任务完成或取消后再撤销、重做或调整图片列表")


def restore_project_state(db: Session, project_id: str, state: dict, prepared: dict) -> None:
    current = {page.id: page for page in db.scalars(select(ImagePage).where(ImagePage.project_id == project_id)).all()}
    wanted = {item["page"]["id"] for item in state["pages"]}
    temporary = max((page.order_index for page in current.values()), default=0) + len(current) + len(state["pages"]) + 1
    for index, page in enumerate(current.values()):
        page.order_index = temporary + index
    db.flush()
    for page_id in state["tracked_page_ids"]:
        if page_id in current and page_id not in wanted:
            db.delete(current[page_id])
    db.flush()
    for index, item in enumerate(state["pages"]):
        attributes = item["page"]
        page = current.get(attributes["id"])
        if page is None:
            page = ImagePage(
                id=attributes["id"],
                project_id=project_id,
                filename=attributes["filename"],
                original_path=attributes["original_path"],
                width=attributes["width"],
                height=attributes["height"],
                order_index=temporary + len(current) + index,
            )
            db.add(page)
            db.flush()
        # All real ordering is assigned together after restoring page contents.
        restore_page_state(db, page, {**item, "page": {**attributes, "order_index": page.order_index}},prepared[attributes["id"]])
    db.flush()
    current = {page.id: page for page in db.scalars(select(ImagePage).where(ImagePage.project_id == project_id)).all()}
    for position in state["order"]:
        if position["id"] in current:
            current[position["id"]].order_index = position["order_index"]
    db.flush()


def _prepare_history_restore(db,project_id,direction,expected_entry_id,operation_id):
    from app.services.workflow_state import current_page_version, fingerprint, record

    if has_pending_writes(db):
        raise ValueError("History restoration requires committed input")
    ensure_project_idle(db,project_id)
    entry = db.get(EditorHistoryEntry,expected_entry_id)
    if entry is None or entry.project_id!=project_id:
        raise HTTPException(409,"没有可撤销或重做的操作")
    if operation_id and any(item.get("id")==operation_id for item in (entry.after or {}).get("_history_commands",[])):
        db.rollback()
        return None  # The locked idempotency check below returns or rejects it.
    _invalidate_state_cache(db,project_id)
    state = project_history_state(db,project_id)
    if state["undo_id" if direction=="undo" else "redo_id"]!=expected_entry_id:
        raise HTTPException(409,"编辑历史已变化，请重试撤销或重做")
    proof = fingerprint(record(entry))
    snapshot = deepcopy(entry.before if direction=="undo" else entry.after)
    pages = snapshot["pages"] if entry.scope=="project" else [snapshot]
    order = list(db.execute(select(ImagePage.id,ImagePage.order_index).where(ImagePage.project_id==project_id)))
    versions,current = {},{}
    for value in pages:
        identifier = value["page"]["id"]
        page = db.get(ImagePage,identifier)
        versions[identifier] = current_page_version(db,identifier) if page else None
        query = select(TextRegion).where(TextRegion.image_id==identifier)
        if "_partial_region_ids" in value:
            query = query.where(TextRegion.id.in_(value["_partial_region_ids"]))
        current[identifier] = {region.id:{"region_key":region.region_key,"snapshot":region_snapshot(region)}
                               for region in db.scalars(query)}
    db.rollback()
    prepared = {value["page"]["id"]:_prepare_restore_page(project_id,value,current[value["page"]["id"]]) for value in pages}
    return {"proof":proof,"versions":versions,"order":sorted(order),"pages":prepared}


def apply_project_history(
    db: Session, project_id: str, direction: str, expected_entry_id: int, *, operation_id: str | None = None
) -> EditorHistoryEntry:
    prepared = _prepare_history_restore(db,project_id,direction,expected_entry_id,operation_id)
    # Only the DB-only portion is replayed after a known transaction abort.
    return retry_write(db,lambda: _apply_project_history(db,project_id,direction,expected_entry_id,operation_id,prepared))


def _apply_project_history(db,project_id,direction,expected_entry_id,operation_id,prepared):
    from app.services.workflow_state import current_page_version, fingerprint, record

    db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
    ensure_project_idle(db, project_id)
    _invalidate_state_cache(db, project_id)
    state = project_history_state(db, project_id)
    wanted = state["undo_id" if direction == "undo" else "redo_id"]
    if operation_id:
        completed = db.get(EditorHistoryEntry, expected_entry_id)
        commands = (completed.after or {}).get("_history_commands", []) if completed else []
        last = next((item for item in commands if item.get("id") == operation_id), None)
        if completed and completed.project_id == project_id and last:
            if (
                completed.applied == (direction == "redo")
                and last.get("direction") == direction
                and last.get("undo_id") == state["undo_id"]
                and last.get("redo_id") == state["redo_id"]
            ):
                return completed
            raise HTTPException(409, "该撤销或重做已执行，后续历史已发生变化")
    if wanted != expected_entry_id:
        raise HTTPException(409, "编辑历史已变化，请重试撤销或重做")
    entry = db.get(EditorHistoryEntry, wanted)
    if entry is None:
        raise HTTPException(409, "没有可撤销或重做的操作")
    if prepared is None or fingerprint(record(entry))!=prepared["proof"]:
        raise HTTPException(409,"编辑历史在文件预检期间发生变化，请重试")
    order = sorted(db.execute(select(ImagePage.id,ImagePage.order_index).where(ImagePage.project_id==project_id)))
    if order!=prepared["order"]:
        raise HTTPException(409,"图片列表在文件预检期间发生变化，请重试")
    # Lock targeted pages in stable order, after taking the project lock.
    for identifier,version in sorted(prepared["versions"].items()):
        page = db.scalar(select(ImagePage).where(ImagePage.id==identifier).with_for_update())
        if (current_page_version(db,identifier) if page else None)!=version:
            raise HTTPException(409,"页面在文件预检期间发生变化，请重试撤销或重做")
    snapshot = entry.before if direction == "undo" else entry.after
    if entry.scope == "project":
        restore_project_state(db, project_id, snapshot,prepared["pages"])
    else:
        page = db.get(ImagePage, entry.image_id)
        if page is None:
            raise HTTPException(409, "请先撤销图片删除，再恢复文字编辑")
        restore_page_state(db, page, snapshot,prepared["pages"][page.id])
    entry.applied = direction == "redo"
    if operation_id:
        db.flush()
        _invalidate_state_cache(db, project_id)
        next_state = project_history_state(db, project_id)
        commands = list((entry.after or {}).get("_history_commands", []))
        commands.append(
            {
                "id": operation_id,
                "direction": direction,
                "undo_id": next_state["undo_id"],
                "redo_id": next_state["redo_id"],
            }
        )
        entry.after = {**entry.after, "_history_commands": commands[-100:]}
    _invalidate_state_cache(db, project_id)
    return entry

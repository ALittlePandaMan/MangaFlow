from __future__ import annotations

import hashlib
import os
import shutil
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from stat import S_ISREG
from uuid import uuid4

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


def _archive_paths(value, page_directory: Path, assets: Path):
    return _SnapshotArchive(page_directory, assets)(value)


def capture_page_state(
    db: Session, page: ImagePage, *, editing: bool = False, region_ids: list[str] | None = None
) -> dict:
    if editing:
        # Serialize read-modify-write history transactions, including multiple
        # browser tabs. Refresh ORM identities only after taking the write lock.
        db.execute(update(ImagePage).where(ImagePage.id == page.id).values(updated_at=ImagePage.updated_at))
        db.refresh(page)
        ensure_page_idle(db, page)
    db.flush()
    query = select(TextRegion).where(TextRegion.image_id == page.id)
    if region_ids is not None:
        query = query.where(TextRegion.id.in_(region_ids))
    regions = db.scalars(
        query.order_by(TextRegion.reading_order, TextRegion.id).execution_options(populate_existing=True)
    ).all()
    if editing and region_ids is not None and {region.id for region in regions} != set(region_ids):
        raise HTTPException(409, "区域列表已变化，请刷新后重试")
    state = {
        "page": {field: deepcopy(getattr(page, field)) for field in PAGE_FIELDS},
        "regions": [
            {"id": region.id, "region_key": region.region_key, **region_snapshot(region)} for region in regions
        ],
    }
    if region_ids is not None:
        state["_partial_region_ids"] = sorted(set(region_ids))
    directory = get_storage().project_dir(page.project_id)
    return _archive_paths(state, directory, directory / "history" / "assets")


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


def record_page_edit(
    db: Session, page: ImagePage, before: dict, action: str, region_ids: list[str] | None = None
) -> EditorHistoryEntry | None:
    after = capture_page_state(db, page, region_ids=before.get("_partial_region_ids"))
    if before == after:
        return None
    entry = EditorHistoryEntry(
        project_id=page.project_id,
        image_id=page.id,
        action=action,
        before=before,
        after=after,
        region_ids=region_ids or [],
    )
    db.add(entry)
    db.flush()
    _finish_entry(db, entry, after)
    return entry


def begin_task_history(db: Session, task: ProcessingTask) -> int | None:
    if not task.page:
        return None
    # Serialize only journal transactions, not whole inference tasks. Pages
    # remain independent; entry IDs preserve dispatch/start order for undo.
    db.execute(update(Project).where(Project.id == task.project_id).values(updated_at=Project.updated_at))
    existing = db.scalar(select(EditorHistoryEntry).where(EditorHistoryEntry.task_id == task.id))
    if existing:
        existing.pending = True
        db.commit()
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
            baseline = capture_page_state(db, task.page)
            originals = {item["id"]: item for item in linked.before["regions"]}
            baseline["regions"] = [originals.get(item["id"], item) for item in baseline["regions"]]
            baseline["page"] = linked.before["page"]
            linked.before = baseline
        linked.task_id = task.id
        linked.pending = True
        db.commit()
        _invalidate_state_cache(db, task.project_id)
        return linked.id
    before = capture_page_state(db, task.page)
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
    db.commit()
    _invalidate_state_cache(db, task.project_id)
    return entry.id


def finish_task_history(db: Session, task_id: str, entry_id: int | None) -> None:
    if entry_id is None:
        return
    db.rollback()
    project_id = db.scalar(select(EditorHistoryEntry.project_id).where(EditorHistoryEntry.id == entry_id))
    if project_id:
        db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
    entry = db.get(EditorHistoryEntry, entry_id)
    task = db.get(ProcessingTask, task_id)
    if not entry:
        return
    page = task.page if task else db.get(ImagePage, entry.image_id)
    if page:
        _finish_entry(db, entry, capture_page_state(db, page))
    else:
        db.delete(entry)
        _invalidate_state_cache(db, entry.project_id)
    db.commit()


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


def restore_page_state(db: Session, page: ImagePage, state: dict) -> None:
    directory = get_storage().relative(get_storage().project_dir(page.project_id) / "history" / "assets") + "/"
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
    query = select(TextRegion).where(TextRegion.image_id == page.id)
    if "_partial_region_ids" in state:
        query = query.where(TextRegion.id.in_(state["_partial_region_ids"]))
    existing = {region.id: region for region in db.scalars(query).all()}
    wanted = {item["id"] for item in state["regions"]}
    project_directory = get_storage().project_dir(page.project_id)
    assets = project_directory / "history" / "assets"
    archive = _SnapshotArchive(project_directory, assets)
    changed = []
    for item in state["regions"]:
        current = existing.get(item["id"])
        # Live masks and archived masks can have different paths but identical
        # content. Use the existing bounded, stat-keyed archive cache so a
        # single-box undo does not rewrite every unrelated region/revision.
        snapshot = archive(region_snapshot(current)) if current else None
        if (
            current is None
            or current.region_key != item["region_key"]
            or any(snapshot[field] != item[field] for field in SNAPSHOT_FIELDS)
        ):
            changed.append(item)
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


def capture_project_state(db: Session, project_id: str, page_ids: list[str], *, editing: bool = False) -> dict:
    if editing:
        db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
        ensure_project_idle(db, project_id)
    db.flush()
    pages = db.scalars(
        select(ImagePage).where(ImagePage.project_id == project_id).order_by(ImagePage.order_index)
    ).all()
    return {
        "pages": [capture_page_state(db, page) for page in pages if page.id in page_ids],
        "tracked_page_ids": page_ids,
        "order": [{"id": page.id, "order_index": page.order_index} for page in pages],
    }


def record_project_edit(
    db: Session, project_id: str, before: dict, action: str, page_ids: list[str], focus_id: str | None = None
) -> None:
    # Imports learn their new IDs after validation; the before snapshot tracks
    # those IDs too so undo removes the entire import as one transaction.
    before = {**before, "tracked_page_ids": page_ids}
    after = capture_project_state(db, project_id, page_ids)
    if before == after:
        return
    entry = EditorHistoryEntry(
        project_id=project_id,
        image_id=focus_id,
        scope="project",
        action=action,
        before=before,
        after=after,
        region_ids=[],
    )
    db.add(entry)
    db.flush()
    _finish_entry(db, entry, after)


def restore_project_state(db: Session, project_id: str, state: dict) -> None:
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
        restore_page_state(db, page, {**item, "page": {**attributes, "order_index": page.order_index}})
    db.flush()
    current = {page.id: page for page in db.scalars(select(ImagePage).where(ImagePage.project_id == project_id)).all()}
    for position in state["order"]:
        if position["id"] in current:
            current[position["id"]].order_index = position["order_index"]
    db.flush()


def apply_project_history(
    db: Session, project_id: str, direction: str, expected_entry_id: int, *, operation_id: str | None = None
) -> EditorHistoryEntry:
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
    snapshot = entry.before if direction == "undo" else entry.after
    if entry.scope == "project":
        restore_project_state(db, project_id, snapshot)
    else:
        page = db.get(ImagePage, entry.image_id)
        if page is None:
            raise HTTPException(409, "请先撤销图片删除，再恢复文字编辑")
        restore_page_state(db, page, snapshot)
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
    db.commit()
    _invalidate_state_cache(db, project_id)
    return entry

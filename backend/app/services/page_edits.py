"""Prepare project-list history and uploads before any application writer."""
from copy import deepcopy

from app.core.transactions import has_pending_writes, project_write
from app.models import EditorHistoryEntry, ImagePage, Project
from app.services.editor_history import _archive_paths, _finish_entry, _page_state, ensure_project_idle
from app.services.workflow_state import current_page_version
from app.storage import get_storage
from fastapi import HTTPException
from sqlalchemy import select, update


def prepare_project_edit(db, project_id, page_ids):
    if has_pending_writes(db):
        raise ValueError("Project edits require committed input")
    ensure_project_idle(db, project_id)
    pages = list(db.scalars(select(ImagePage).where(ImagePage.project_id == project_id)
                            .order_by(ImagePage.order_index, ImagePage.id)
                            .execution_options(populate_existing=True)))
    selected = [page for page in pages if page.id in page_ids]
    versions = {page.id: current_page_version(db, page.id) for page in selected}
    before = {"pages": [_page_state(db, page) for page in selected], "tracked_page_ids": list(page_ids),
              "order": [{"id": page.id, "order_index": page.order_index} for page in pages]}
    db.rollback()
    directory = get_storage().project_dir(project_id)
    return {"project_id": project_id, "versions": versions,
            "before": _archive_paths(before, directory, directory / "history" / "assets")}


def publish_project_edit(db, prepared, after, operation, action, focus_id=None):
    project_id = prepared["project_id"]
    directory = get_storage().project_dir(project_id)
    # New original images, or images about to be deleted, are archived here,
    # never from the replayable DB operation below.
    before = prepared["before"]
    after = _archive_paths(after, directory, directory / "history" / "assets")

    def save():
        db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
        if db.get(Project, project_id) is None:
            raise HTTPException(404, "Project not found")
        ensure_project_idle(db, project_id)
        pages = list(db.scalars(select(ImagePage).where(ImagePage.project_id == project_id)
                                .order_by(ImagePage.id).with_for_update()))
        actual = sorted((page.id, page.order_index) for page in pages)
        expected = sorted((item["id"], item["order_index"]) for item in before["order"])
        if actual != expected or any(current_page_version(db, identifier) != version
                                     for identifier, version in prepared["versions"].items()):
            raise HTTPException(409, "图片列表或页面内容已变化，请刷新后重试；未覆盖其他编辑")
        operation(db)
        db.flush()
        if before != after:
            entry = EditorHistoryEntry(project_id=project_id, image_id=focus_id, scope="project",
                action=action, before=deepcopy(before), after=deepcopy(after), region_ids=[])
            db.add(entry)
            db.flush()
            _finish_entry(db, entry, after)

    project_write(db, project_id, save)
    db.expire_all()


def apply_page_order(db, project_id, order):
    """DB-only two-phase assignment preserves the unique page-order key."""
    pages = {page.id: page for page in db.scalars(select(ImagePage).where(ImagePage.project_id == project_id))}
    temporary = max((page.order_index for page in pages.values()), default=0) + len(pages) + 1
    for index, page in enumerate(pages.values()):
        page.order_index = temporary + index
    db.flush()
    for item in order:
        pages[item["id"]].order_index = item["order_index"]

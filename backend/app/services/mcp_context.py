"""Request-local optimistic version fences, propagated into FastAPI workers."""
from contextvars import ContextVar

from fastapi import HTTPException

expected_versions: ContextVar[dict[str, str] | None] = ContextVar("mcp_expected_versions", default=None)
operation_source: ContextVar[str | None] = ContextVar("mcp_operation_source", default=None)
operation_identifier: ContextVar[str | None] = ContextVar("mcp_operation_identifier", default=None)
workflow_run_id: ContextVar[str | None] = ContextVar("mcp_workflow_run_id", default=None)
workflow_action: ContextVar[str] = ContextVar("mcp_workflow_action", default="")
coordination_credentials: ContextVar[dict | None] = ContextVar("mcp_coordination_credentials", default=None)


def check_page_version(db, page_id):
    versions = expected_versions.get()
    if versions is None:
        return
    if operation_source.get() == "MCP":
        from app.services.mcp_coordination import check_page_access
        check_page_access(db, page_id)
    from app.services.workflow_state import current_page_version
    try:
        actual = current_page_version(db, page_id)
    except ValueError:
        from app.models import ImagePage
        if db.get(ImagePage, page_id, populate_existing=True) is not None:
            raise
        actual = "deleted"
    if page_id not in versions or versions[page_id] != actual:
        raise HTTPException(409, "Page version changed; fetch page_state and inspect the latest image before editing")
    if workflow_run_id.get():
        from app.services.mcp_workflows import guard_page
        guard_page(db, workflow_run_id.get(), page_id, mutation=workflow_action.get() != "review_record")
    return actual


def commit_page_tasks(db, project_id, page_ids, create):
    """Check MCP input versions and enqueue atomically, without locking inference.

    Browser requests retain their existing queue behavior. Versions describe
    admission, not execution: queued stages may consume their predecessor's
    published results on the same page.
    """
    if expected_versions.get() is None:
        result = create({})
        mark_submission_source(result)
        db.commit()
        return result
    from app.core.transactions import project_write
    from app.models import ImagePage, Project
    from sqlalchemy import select, update

    def save():
        db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
        pages = list(db.scalars(select(ImagePage).where(ImagePage.project_id == project_id,
            ImagePage.id.in_(page_ids)).order_by(ImagePage.id).with_for_update()))
        if {page.id for page in pages} != set(page_ids):
            raise HTTPException(409, "Selected pages changed before task submission")
        versions = {page.id: check_page_version(db, page.id) for page in pages}
        result = create(versions)
        mark_submission_source(result)
        if workflow_run_id.get():
            from app.models import ProcessingTask
            from app.services.mcp_workflows import prepare_task
            for task in result if isinstance(result, list) else [result]:
                if isinstance(task, ProcessingTask):
                    prepare_task(db, task, workflow_run_id.get(), workflow_action.get())
        return result

    return project_write(db, project_id, save)


def mark_submission_source(result):
    from app.models import ProcessingTask
    for task in result if isinstance(result, list) else [result]:
        if isinstance(task, ProcessingTask):
            options = dict(task.payload.get("options", {}))
            if operation_source.get() == "MCP":
                options["submitted_via"] = "mcp"
                if operation_identifier.get():
                    options["mcp_operation_id"] = operation_identifier.get()
            else:
                options.pop("submitted_via", None)
            task.payload = {**task.payload, "options": options}


def history_versions(cursors):
    """Expose live or deleted-page version tokens for undo/redo targets."""
    from app.core.database import SessionLocal
    from app.models import EditorHistoryEntry, ImagePage
    from app.services.workflow_state import current_page_version

    identifiers = set()
    with SessionLocal() as db:
        for key in ("undo_id", "redo_id"):
            entry = db.get(EditorHistoryEntry, cursors[key]) if cursors.get(key) else None
            if not entry or entry.project_id != cursors.get("project_id"):
                continue
            identifiers.update(entry.before.get("tracked_page_ids", []) if entry.scope == "project" else [entry.image_id])
        return {identifier: current_page_version(db, identifier) if db.get(ImagePage, identifier) else "deleted"
                for identifier in sorted(identifiers) if identifier}

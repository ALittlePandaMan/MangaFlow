"""Publish prepared stage values, file references and one undo entry atomically."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from app.core.transactions import project_write
from app.models import EditorHistoryEntry, ImagePage, ProcessingTask, Project, StageCheckpoint, TaskWorkflow, TextRegion
from app.services.checkpoints import StaleExecution
from app.services.editor_history import PAGE_FIELDS, _archive_paths, _finish_entry
from app.services.regions import SNAPSHOT_FIELDS
from app.services.workflow_state import COLLECTIONS, current_page_version, decode, delta, requested_stages
from PIL import Image
from sqlalchemy import select, update

PAGE_OUTPUT_FIELDS = {"clean_path", "rendered_path", "text_layer_path", "metadata_json", "status", "current_stage", "error_message"}


def prepare_history(state: dict, storage) -> dict:
    """Do all filesystem archiving outside the publication transaction."""
    project_dir = storage.project_dir(state["page"]["project_id"])
    regions = sorted(state["regions"].values(), key=lambda row: (row["reading_order"], row["created_at"], row["id"]))
    snapshot = {"page": {field: deepcopy(state["page"][field]) for field in PAGE_FIELDS},
                "regions": [{field: deepcopy(row[field]) for field in ("id", "region_key", *SNAPSHOT_FIELDS)} for row in regions]}
    return _archive_paths(snapshot, project_dir, project_dir / "history" / "assets")


def publish_workflow(store, lease, initial: dict, result: dict, history_entry_id: int, *, proofs: dict[str, str]) -> bool:
    with store.sessions() as db:
        task, workflow = db.get(ProcessingTask, lease.task_id), db.get(TaskWorkflow, lease.id)
        if task and workflow and task.execution_generation == lease.generation and workflow.generation == lease.generation:
            if task.status == "COMPLETED" and workflow.status == "PUBLISHED":
                return False
        if workflow is None:
            raise StaleExecution("Workflow is missing")
        required = set(requested_stages(workflow.policy["request"]))
    if set(proofs) != required:
        raise ValueError("The complete requested stage dependency chain is required")
    identifiers = store.validate_proofs(lease, proofs)
    required_images = ({"clean_path"} if "inpainting" in required else set()) | (
        {"rendered_path", "text_layer_path"} if "rendering" in required else set())
    for field in ("clean_path", "rendered_path", "text_layer_path"):
        path = result["page"].get(field)
        if field in required_images and not path:
            raise ValueError("A required published image is missing")
        if not path or (field not in required_images and path == initial["page"].get(field)):
            continue
        with Image.open(store.storage.absolute(path)) as image:
            if image.size != (initial["page"]["width"], initial["page"]["height"]):
                raise ValueError("Workflow output dimensions changed")
    changes = delta(initial, result)
    if set(changes["page"]) - PAGE_OUTPUT_FIELDS:
        raise ValueError("Workflow cannot replace the original page identity, image or ordering")
    removed = set(changes["regions"]["delete"])
    if removed and "detection" not in required:
        raise ValueError("Only redetection can replace existing text regions")
    for name in ("revisions", "translations"):
        if any(initial[name][identifier]["region_id"] not in removed for identifier in changes[name]["delete"]):
            raise ValueError("Workflow cannot delete history belonging to retained regions")
    for collection in ("revisions",):
        if set(changes[collection]["put"]) & set(initial[collection]):
            raise ValueError("Workflow cannot rewrite old history")
    history = prepare_history(result, store.storage)
    project_id, page_id = initial["page"]["project_id"], initial["page"]["id"]
    with store.sessions() as db:
        def save():
            # Match editor history's project -> page/journal locking order.
            db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
            project = db.get(Project, project_id, populate_existing=True)
            task = db.scalar(select(ProcessingTask).where(ProcessingTask.id == lease.task_id).with_for_update())
            # Submitted batches deliberately keep their frozen language/style
            # even if settings later change for newly submitted work.
            if project is None or (not (task and task.batch_id) and any(getattr(project, key) != initial["project"][key] for key in (
                "source_language", "target_language", "translation_context", "settings",
            ))):
                raise StaleExecution("项目翻译配置在计算期间发生变化，未发布过期结果")
            workflow = db.get(TaskWorkflow, lease.id)
            if task is None or workflow is None or task.execution_generation != lease.generation or workflow.generation != lease.generation:
                raise StaleExecution("Superseded task cannot publish")
            if workflow.status == "PUBLISHED" and task.status == "COMPLETED":
                return False  # Idempotent after an acknowledged publication.
            if task.status != "RUNNING" or task.pause_requested or workflow.status != "RUNNING":
                raise StaleExecution("Stopped task cannot publish")
            current = set(db.scalars(select(StageCheckpoint.id).where(
                StageCheckpoint.id.in_(identifiers), StageCheckpoint.workflow_id == lease.id,
                StageCheckpoint.status == "SUCCEEDED",
            )))
            if current != set(identifiers):
                raise StaleExecution("Stage proof changed before publication")
            page = db.scalar(select(ImagePage).where(ImagePage.id == page_id).with_for_update())
            if page is None or current_page_version(db, page_id) != lease.page_version:
                raise StaleExecution("页面在计算期间发生变化，未发布过期结果")
            entry = db.get(EditorHistoryEntry, history_entry_id)
            if entry is None or entry.task_id != lease.task_id or entry.image_id != page_id or not entry.pending:
                raise StaleExecution("Workflow undo record is no longer pending")
            for key, value in changes["page"].items():
                setattr(page, key, deepcopy(value))
            for identifier in changes["regions"]["delete"]:
                region = db.get(TextRegion, identifier)
                if region is not None:
                    db.delete(region)
            db.flush()
            for name, model in COLLECTIONS.items():
                for identifier, values in changes[name]["put"].items():
                    row = db.get(model, identifier)
                    if identifier not in initial[name]:
                        if row is not None:
                            raise StaleExecution("Workflow result identifier already exists")
                        db.add(model(**decode(model, values)))
                    else:
                        if row is None:
                            raise StaleExecution("Workflow input row was deleted")
                        for key, value in decode(model, values).items():
                            setattr(row, key, value)
                db.flush()
            _finish_entry(db, entry, history)
            workflow.status = "PUBLISHED"
            task.status, task.progress, task.current_stage = "COMPLETED", 1.0, None
            task.error_message, task.message = None, "Completed"
            task.waiting_reason, task.error_kind, task.active_stages = None, None, []
            task.finished_at = datetime.now(timezone.utc)
            return True
        return project_write(db, project_id, save)

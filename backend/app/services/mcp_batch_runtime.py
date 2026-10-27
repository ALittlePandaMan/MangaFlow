"""Batch advice and atomic admission over the existing per-page tasks."""
from copy import deepcopy
from uuid import uuid4

from app.core.database import SessionLocal
from app.models import ImagePage, MCPOperation, ProcessingTask
from app.services.mcp_batch_workflow import enabled
from app.services.mcp_workflow_steps import step
from fastapi import HTTPException
from sqlalchemy import select


def advice(run, snapshots):
    if not enabled(run):
        return None
    values = [item[0] for item in snapshots]
    text, output, reviews, corrections, agents, editing = [], [], [], [], [], []
    tasks = {task["id"]: task for value in values for task in value["tasks"]}
    active = [task for task in tasks.values() if task["status"] in {"QUEUED", "RUNNING", "PAUSED", "CANCELLING"}]
    for index, (value, raw) in enumerate(snapshots):
        process = next((item for item in raw if item["tool"] == "images_process_image"), None)
        if process:
            (text if process["arguments"]["body"]["options"].get("_workflow_text_only") else output).append(index)
        stage = value["execution_stage"]
        if stage in {"preflight_review", "final_review", "stale_review", "delivery_decision"}:
            reviews.append(index)
        elif stage == "correction_available":
            corrections.append(index)
        elif stage == "agent_translation":
            agents.append(index)
        elif stage == "correcting":
            editing.append(index)
    initial = run.options.get("batch_phase", "text") == "text"
    phase = "text" if initial and (text or agents or active) else "output"
    output_first = output if run.options.get("output_started") else []
    choices = text or agents or output_first or reviews or ([] if active else editing or corrections) or (output if phase == "output" else [])
    if choices:
        current = choices[0]
    else:
        current = next((i for i, value in enumerate(values) if value["execution_stage"] != "ready_for_delivery"), len(values))
    steps = values[current]["next_steps"] if current < len(values) else []
    if choices and (choices is text or choices is output):
        selected = [values[i] for i in choices[:20]]
        steps = [step("workflow_advance", {"run_id": run.id, "image_ids": [value["image_id"] for value in selected],
            "expected_versions": {value["image_id"]: value["version"] for value in selected}},
            "Queue this batch atomically, then inspect other finished pages instead of waiting on its first task.", required_inputs=["operation_id"])]
    elif active and not choices:
        # Prefer a real running task over a paused head page; never resume it.
        task = next((task for task in active if task["status"] != "PAUSED"), active[0])
        current = next(i for i, value in enumerate(values) if any(t["id"] == task["id"] for t in value["tasks"]))
        steps = [step("task_wait", {"task_id": task["id"], "kind": "processing", "seconds": 0 if task["status"] == "PAUSED" else 20},
            "No finished page needs review. Wait for published results; paused work needs user direction.", automatic=task["status"] != "PAUSED")]
    return {"current": current, "next_steps": steps, "summary": {"phase": phase,
        "queued": sum(t["status"] == "QUEUED" for t in tasks.values()),
        "running": sum(t["status"] in {"RUNNING", "CANCELLING"} for t in tasks.values()),
        "paused": sum(t["status"] == "PAUSED" for t in tasks.values()),
        "completed": sum(t["status"] == "COMPLETED" for t in tasks.values()),
        "failed": sum(t["status"] == "FAILED" for t in tasks.values()),
        "cancelled": sum(t["status"] == "CANCELLED" for t in tasks.values()),
        "review_ready": len(reviews), "correction_pending": len(corrections)+len(editing), "text_pending": len(text)+len(agents)}}


def advance_batch(args):
    from app.services.mcp_actions import compact_task
    from app.services.mcp_context import commit_page_tasks, operation_identifier, workflow_action, workflow_run_id
    from app.services.mcp_workflow_runtime import snapshot
    from app.services.mcp_workflows import configuration, page_row, run_row
    from app.tasks import task_manager
    identifiers = args["image_ids"]
    if args.get("image_id") and args["image_id"] not in identifiers or not identifiers or len(identifiers) > 20 or len(set(identifiers)) != len(identifiers):
        raise HTTPException(422, "Supply image_id OR 1-20 unique image_ids")
    with SessionLocal() as db:
        run = run_row(db, args["run_id"])
        run_id, project_id = run.id, run.project_id
        token, action = workflow_run_id.set(run_id), workflow_action.set("workflow_advance")
        skipped_result = []
        def create(versions):
            from app.api.helpers import require_project
            from app.services.batch_context import prepare_batch
            from app.services.mcp_batch_workflow import assert_correction_barrier, correction_phase
            stale = configuration(require_project(db, project_id)) != run.options["configuration"]
            planned, skipped = [], []
            for identifier in identifiers:
                page = db.get(ImagePage, identifier)
                receipt = page_row(db, run_id, identifier)
                value, steps = snapshot(db, run, receipt, stale)
                process = next((item for item in steps if item["tool"] == "images_process_image"), None)
                if process:
                    planned.append((page, deepcopy(process["arguments"]["body"])))
                else:
                    skipped.append({"image_id": identifier, "next_steps": value["next_steps"]})
            if any(not body["options"].get("_workflow_text_only") for _, body in planned) and enabled(run):
                if run.options.get("batch_phase", "text") == "text":
                    assert_correction_barrier(db, run)
                    correction_phase(run)
                run.options = {**run.options, "output_started": True}
                # Later chunks of the same output wave can enqueue while its
                # earlier chunks run; corrections themselves remain fenced.
            text_pages = [page for page, body in planned if body["options"].get("_workflow_text_only")]
            batch = prepare_batch(db, require_project(db, project_id), text_pages,
                {"start_stage": "detection", "end_stage": "translation", "options": {}}, persist=True, provision=False,
                page_order=run.page_ids) if text_pages and run.options["translation_mode"] == "configured" else None
            ids = {page.id: str(uuid4()) for page, _ in planned}
            tasks = []
            for page, body in planned:
                text_only = body["options"].get("_workflow_text_only")
                previous = (batch.contexts.get(page.id) or {}).get("page_id") if batch and text_only else None
                dependency = ids.get(previous) if batch and batch.context_mode == "previous_page" and text_only else None
                if batch and previous and not dependency and batch.context_mode == "previous_page" and text_only:
                    prior_task = db.scalar(select(ProcessingTask).where(ProcessingTask.image_id == previous,
                        ProcessingTask.payload["options"]["workflow_run_id"].as_string() == run_id,
                        ProcessingTask.payload["options"]["_workflow_text_only"].as_boolean().is_(True))
                        .order_by(ProcessingTask.created_at.desc()).limit(1))
                    # Completed task checkpoints may predate manual corrections.
                    # Their current published text is already frozen in batch.contexts.
                    dependency = prior_task.id if prior_task and prior_task.status != "COMPLETED" else None
                task = ProcessingTask(id=ids[page.id], project_id=project_id, image_id=page.id,
                    task_type="full", status="QUEUED", message="Queued by MCP batch",
                    payload={**body, "batch_submission": True, "_mcp_admission_version": versions[page.id]},
                    batch_id=batch.id if batch and text_only else None, dependency_task_id=dependency)
                db.add(task)
                tasks.append(task)
            # The manifest commits with ALL task rows. UNKNOWN recovery reads
            # it even if the process disappears before the response is saved.
            op = db.get(MCPOperation, operation_identifier.get()) if operation_identifier.get() else None
            if op:
                op.result = {**op.result, "_admission": {"task_ids": [task.id for task in tasks], "skipped": skipped,
                    "single": bool(args.get("image_id"))}}
            skipped_result[:] = skipped
            return tasks
        try:
            tasks = commit_page_tasks(db, project_id, identifiers, create)
            result = {"run_id": run_id, "tasks": [compact_task(task) for task in tasks], "skipped": skipped_result, "next_tool": "workflow_status"}
            for task in tasks:
                task_manager.dispatch(task.id)
            return result
        finally:
            workflow_action.reset(action)
            workflow_run_id.reset(token)

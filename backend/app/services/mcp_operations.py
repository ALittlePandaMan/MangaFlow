"""Recover mutation receipts, never replay uncertain business/provider work."""
import json

from app.core.database import SessionLocal
from app.core.transactions import project_write
from app.models import FileJob, MCPOperation, ProcessingTask, StageCheckpoint, TaskWorkflow
from app.services.mcp_actions import compact_task
from app.services.workflow_state import current_page_version
from fastapi import HTTPException
from sqlalchemy import select


def status(args):
    with SessionLocal() as db:
        row = db.get(MCPOperation, args["operation_id"])
        if not row:
            raise HTTPException(404, "Operation not found")
        if row.status == "UNKNOWN":
            if row.result.get("_coordination") is not None:
                from app.services.mcp_server import result
                row.status, row.result = "COMPLETED", result(row.result["_coordination"]).model_dump(mode="json")
                db.commit()
            # Admission is atomic with this exact tag. Finding the task proves
            # acceptance, not successful inference; normal task safety still applies.
            tool = (row.result or {}).get("_request", {}).get("tool")
            action = (row.result or {}).get("_request", {}).get("action")
            if tool == "workflow_advance" and action in {"claim", "renew", "release", "accept_configuration"} and "_coordination" not in row.result:
                from app.services.mcp_server import result
                # Lease changes and their receipt commit together. No receipt
                # after interruption means no lease change was committed.
                row.status, row.result = "COMPLETED", result({"run_id": row.workflow_run_id, "applied": False,
                    "message": "Workflow action did not commit. Inspect current state, then use a new operation_id.",
                    "next_tool": "workflow_status"}).model_dump(mode="json")
                db.commit()
            manifest = row.result.get("_admission")
            if tool == "workflow_advance" and manifest is not None:
                from app.services.mcp_server import result
                tasks = list(db.scalars(select(ProcessingTask).where(ProcessingTask.id.in_(manifest["task_ids"]))))
                if {task.id for task in tasks} == set(manifest["task_ids"]):
                    data = {"run_id": row.workflow_run_id, "tasks": [compact_task(task) for task in tasks],
                        "skipped": manifest["skipped"], "next_tool": "workflow_status", "recovered": True}
                    if manifest.get("single") and len(tasks) == 1:
                        data.update(image_id=tasks[0].image_id, task=compact_task(tasks[0]))
                    row.status, row.result = "COMPLETED", result(data).model_dump(mode="json")
                    db.commit()
            if row.status == "COMPLETED":
                return {"operation_id": row.id, "status": row.status,
                    "result": row.result["structuredContent"], "isError": False}
            task = db.scalar(select(ProcessingTask).where(
                ProcessingTask.payload["options"]["mcp_operation_id"].as_string() == row.id)) if tool in {"workflow_advance", "task_retry"} and not row.result.get("_request", {}).get("image_ids") and manifest is None else None
            job = db.scalar(select(FileJob).where(
                FileJob.result["import_operation_id" if tool == "project_import" else "mcp_operation_id"].as_string() == row.id)) if tool in {"project_import", "workflow_export"} else None
            if task or job:
                from app.services.mcp_server import result
                if task:
                    data = ({"run_id": row.workflow_run_id, "image_id": task.image_id,
                        "task": compact_task(task), "next_tool": "task_wait", "recovered": True}
                        if tool == "workflow_advance" else {**compact_task(task), "recovered": True})
                else:
                    from app.services.file_jobs import job_read
                    data = {"project_id": job.project_id, "job": compact_task(job_read(job), "file"),
                        "source_fingerprint": job.result.get("source_fingerprint"), "next_tool": "task_wait", "recovered": True}
                    if tool == "workflow_export":
                        data["report_downloads"] = {kind: f"/mangaflow/reports/{job.id}/{kind}" for kind in ("json", "markdown")}
                row.status, row.result = "COMPLETED", result(data).model_dump(mode="json")
                db.commit()
        if args.get("accept_current_state") and row.status == "UNKNOWN":
            if row.workflow_run_id:
                from app.services.mcp_coordination import coordinator
                from app.services.mcp_workflows import run_row
                coordinator(run_row(db, row.workflow_run_id))
            acknowledge(db, row, args)
        response = row.result or {}
        data = response.get("structuredContent")
        if data is None:
            data = next((json.loads(item["text"]) for item in response.get("content", []) if item["type"] == "text"), {})
        output = {"operation_id": row.id, "status": row.status, "result": data, "isError": response.get("isError", False)}
        if row.status == "UNKNOWN" and row.workflow_run_id:
            output["recovery"] = {"run_id": row.workflow_run_id, "tool": "operation_status",
                "arguments": {"operation_id": row.id, "accept_current_state": True},
                "required_inputs": ["expected_versions for every frozen page", "note"],
                "inspect": {"tool": "workflow_status", "arguments": {"run_id": row.workflow_run_id, "offset": 0, "limit": 50}},
                "reason": "Inspect current pages and tasks first. Acceptance preserves unknown outcome and never retries it; active or ambiguous paid work blocks acceptance."}
        return output


def acknowledge(db, row, args):
    from app.services.mcp_workflows import ACTIVE, lock_run_project, run_row
    if not row.workflow_run_id or not args.get("note", "").strip():
        raise HTTPException(422, "Current-state recovery requires a workflow run and an inspection note")
    run = run_row(db, row.workflow_run_id)
    identifier = row.id

    def save():
        lock_run_project(db, run)
        current = db.scalar(select(MCPOperation).where(MCPOperation.id == identifier).with_for_update())
        if current.status != "UNKNOWN":
            return
        versions = args.get("expected_versions", {})
        if set(versions) != set(run.page_ids) or any(versions[key] != current_page_version(db, key) for key in run.page_ids):
            raise HTTPException(409, "Inspect every frozen page and supply its current expected_version before accepting state")
        tasks = select(ProcessingTask.id).where(ProcessingTask.image_id.in_(run.page_ids))
        active = db.scalar(tasks.where(ProcessingTask.status.in_(ACTIVE)).limit(1))
        files = db.scalar(select(FileJob.id).where(FileJob.project_id == run.project_id,
            FileJob.status.in_(["QUEUED", "RUNNING", "CANCELLING"])).limit(1))
        uncertain = db.scalar(select(StageCheckpoint.id).join(TaskWorkflow).where(
            TaskWorkflow.task_id.in_(tasks), StageCheckpoint.status == "UNKNOWN").limit(1))
        uncertain_task = db.scalar(tasks.where(ProcessingTask.error_kind == "unknown_cloud_outcome").limit(1))
        running = db.scalar(select(MCPOperation.id).where(MCPOperation.workflow_run_id == run.id,
            MCPOperation.status == "RUNNING", MCPOperation.id != identifier).limit(1))
        if active or files or running or uncertain or uncertain_task:
            raise HTTPException(409, "Active work or an ambiguous paid request remains; inspect task_wait/web task controls before recovery")
        current.status = "ACKNOWLEDGED"
        current.result = {"structuredContent": {"run_id": run.id, "original_outcome": "unknown",
            "accepted_versions": versions, "note": args["note"], "replayed": False, "next_tool": "workflow_status"}}

    project_write(db, run.project_id, save)

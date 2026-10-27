"""Explicit configuration recovery in the same run, without replaying page work."""
from copy import deepcopy

from app.api.helpers import require_project
from app.core.database import SessionLocal
from app.core.transactions import project_write
from app.models import FileJob, MCPOperation, MCPWorkflowPage, ProcessingTask
from app.models.entities import utcnow
from app.services.mcp_context import operation_identifier
from app.services.mcp_workflow_steps import step
from app.services.workflow_state import current_page_version, fingerprint
from fastapi import HTTPException
from sqlalchemy import select


def recovery_steps(db, run, receipts, values):
    from app.services.mcp_coordination import live, parallel
    from app.services.mcp_workflows import configuration
    current = configuration(require_project(db, run.project_id))
    changed = [key for key in current if current[key] != run.options["configuration"].get(key)]
    change = {"version": fingerprint(current), "changed_fields": changed,
        "source_language": current["source_language"], "target_language": current["target_language"]}
    if any(live(row.assignment) for row in receipts.values()):
        return change, [step("workflow_status", {"run_id": run.id},
            "Workers must release their pages (or leases expire) before accepting the configuration change.", automatic=False)]
    tasks = list(db.scalars(select(ProcessingTask).where(ProcessingTask.image_id.in_(run.page_ids),
        ProcessingTask.status.in_(["QUEUED", "RUNNING", "PAUSED", "CANCELLING"]))))
    jobs = list(db.scalars(select(FileJob).where(FileJob.project_id == run.project_id,
        FileJob.status.in_(["QUEUED", "RUNNING", "CANCELLING"]))))
    if tasks or jobs:
        return change, [step("task_wait", {"task_id": item.id, "kind": kind, "seconds": 0},
            "Inspect unfinished work before configuration recovery; do not resume paused/cancelled work automatically.", automatic=False)
            for kind, items in (("processing", tasks), ("file", jobs)) for item in items[:5]]
    return change, [step("workflow_advance", {"run_id": run.id, "action": "accept_configuration",
        "configuration_version": change["version"], "expected_versions": {value["image_id"]: value["version"] for value in values}},
        "Inspect the changed configuration, then explicitly accept it. Existing page content and correction counts remain; prior reviews are archived and must be renewed.",
        required_inputs=["operation_id", *(["coordinator_token"] if parallel(run) else [])], automatic=False)]


def accept(args):
    from app.services.mcp_coordination import coordinator, live, owners
    from app.services.mcp_workflows import ACTIVE, configuration, lock_run_project, run_row
    if args.get("image_id") or args.get("image_ids") or args.get("worker_id") or args.get("assignment_token"):
        raise HTTPException(422, "Configuration recovery applies to the whole run and coordinator only")
    with SessionLocal() as db:
        run = run_row(db, args["run_id"])

        def save():
            lock_run_project(db, run)
            coordinator(run)
            if run.options.get("superseded_by"):
                raise HTTPException(409, "Workflow was superseded; resume the newer run")
            current = configuration(require_project(db, run.project_id))
            if args["configuration_version"] != fingerprint(current):
                raise HTTPException(409, "Configuration changed again; inspect workflow_status before accepting")
            versions = args["expected_versions"]
            if set(versions) != set(run.page_ids) or any(versions[key] != current_page_version(db, key) for key in run.page_ids):
                raise HTTPException(409, "Inspect all frozen pages and supply their current versions before accepting configuration")
            if any(owners(db, key, exclude_run_id=run.id) for key in run.page_ids):
                raise HTTPException(409, "Another active parallel run owns these pages")
            receipts = list(db.scalars(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run.id).with_for_update()))
            if any(live(row.assignment) for row in receipts):
                raise HTTPException(409, "Release all worker pages before accepting configuration")
            active = db.scalar(select(ProcessingTask.id).where(ProcessingTask.image_id.in_(run.page_ids),
                ProcessingTask.status.in_(ACTIVE)).limit(1))
            files = db.scalar(select(FileJob.id).where(FileJob.project_id == run.project_id,
                FileJob.status.in_(["QUEUED", "RUNNING", "CANCELLING"])).limit(1))
            operations = db.scalar(select(MCPOperation.id).where(MCPOperation.workflow_run_id == run.id,
                MCPOperation.id != operation_identifier.get(), MCPOperation.status.in_(["RUNNING", "UNKNOWN"])).limit(1))
            if active or files or operations:
                raise HTTPException(409, "Finish or inspect active/unknown work before accepting configuration")
            changed = current != run.options["configuration"]
            if changed:
                archive = {"configuration": deepcopy(run.options["configuration"]), "archived_at": utcnow().isoformat(),
                    "pages": [{"image_id": row.image_id, "preflight": row.preflight, "final_review": row.final_review}
                        for row in receipts if row.preflight or row.final_review]}
                run.options = {**{key: value for key, value in run.options.items()
                    if key not in {"text_corrections_ready", "output_corrections_ready"}},
                    "configuration": current, "configuration_history": [*run.options.get("configuration_history", []), archive],
                    "batch_phase": "text", "output_started": False, "coordination_closed": False}
                for row in receipts:
                    row.preflight, row.final_review, row.assignment = {}, {}, {}
            response = {"run_id": run.id, "configuration_accepted": True, "reviews_invalidated": changed,
                "page_count": len(run.page_ids), "next_tool": "workflow_status"}
            op = db.get(MCPOperation, operation_identifier.get())
            if op:
                op.result = {**op.result, "_coordination": response}
            return response

        return project_write(db, run.project_id, save)

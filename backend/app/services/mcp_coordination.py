"""Durable page leases for client-native agents; inference stays in the queue."""
import hashlib
import secrets
from datetime import datetime, timedelta

from app.api.helpers import require_project
from app.core.database import SessionLocal
from app.core.transactions import project_write
from app.models import ImagePage, MCPOperation, MCPWorkflowPage, MCPWorkflowRun
from app.models.entities import utcnow
from app.services.mcp_context import coordination_credentials, operation_identifier, workflow_action, workflow_run_id
from app.services.mcp_workflow_steps import step
from app.services.workflow_state import current_page_version
from fastapi import HTTPException
from sqlalchemy import select

LEASE_SECONDS = 15*60
REVIEW_STAGES = {"preflight_review", "final_review", "stale_review", "delivery_decision", "preflight_unresolved", "failed", "cancelled"}
CORRECTION_STAGES = {"correction_available", "correcting"}


def parallel(run):
    return run.options.get("coordination_mode") == "parallel"


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def same_token(candidate, expected):
    return secrets.compare_digest(candidate.encode(), expected.encode())


def live(assignment):
    return bool(assignment and datetime.fromisoformat(assignment["expires_at"]) > utcnow())


def coordinator(run, credentials=None):
    if not parallel(run):
        return
    if run.options.get("superseded_by"):
        raise HTTPException(409, "This workflow was superseded; resume the newer run instead of writing the old run")
    supplied = (credentials or coordination_credentials.get() or {}).get("coordinator_token", "")
    if not supplied or not secrets.compare_digest(token_hash(supplied), run.options["coordinator_hash"]):
        raise HTTPException(409, "Coordinator credential required for batch processing, task control and export")


def active_run(db, run):
    if run.options.get("coordination_closed") or run.options.get("superseded_by"):
        return False
    rows = list(db.scalars(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run.id)))
    return any(live(row.assignment) or row.correction_open or not row.final_review
        or row.final_review.get("version") != current_page_version(db, row.image_id)
        or row.final_review.get("outcome") == "issues" and row.final_review.get("disposition") != "deliver_with_issues"
        for row in rows if db.get(ImagePage, row.image_id))


def owners(db, page_id, *, exclude_run_id=None):
    query = select(MCPWorkflowRun).join(MCPWorkflowPage, MCPWorkflowPage.run_id == MCPWorkflowRun.id).where(
        MCPWorkflowPage.image_id == page_id, MCPWorkflowRun.options["coordination_mode"].as_string() == "parallel")
    return [run for run in db.scalars(query) if run.id != exclude_run_id and active_run(db, run)]


def check_page_access(db, page_id):
    credentials = coordination_credentials.get() or {}
    supplied = credentials.get("workflow_run_id") or credentials.get("run_id") or workflow_run_id.get()
    runs = owners(db, page_id, exclude_run_id=supplied)
    explicit = db.get(MCPWorkflowRun, supplied) if supplied else None
    if explicit and explicit.options.get("superseded_by"):
        raise HTTPException(409, "Workflow was superseded; old worker credentials cannot write")
    if explicit and parallel(explicit) and explicit not in runs:
        runs.append(explicit)
    for run in runs:
        if supplied != run.id:
            raise HTTPException(409, "Page belongs to a parallel workflow; supply its run ID and current page assignment")
        row = db.scalar(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run.id, MCPWorkflowPage.image_id == page_id)
            .execution_options(populate_existing=True))
        if not row:
            raise HTTPException(422, "Page is outside this workflow")
        action = workflow_action.get()
        if (action == "page_edit" and credentials.get("coordinator_token") and run.options["translation_mode"] == "agent"
                and not row.preflight and not row.final_review):
            coordinator(run)
            if live(row.assignment):
                raise HTTPException(409, "Release the worker's page before coordinator text preparation")
            continue  # page_edit separately restricts this path to translated_text.
        if action in {"workflow_advance", "task_retry", "task_cancel"} and credentials.get("coordinator_token"):
            coordinator(run)
            if action == "workflow_advance" and live(row.assignment):
                raise HTTPException(409, "Worker still owns this page; release it before scheduling model work")
            continue
        assignment = row.assignment
        if (not live(assignment) or credentials.get("worker_id") != assignment["worker_id"]
                or not same_token(credentials.get("assignment_token", ""), assignment["token"])):
            raise HTTPException(409, "Page assignment is missing, expired or owned by another worker; claim again")
        if action in {"page_edit", "mask_paint"} and not row.correction_open:
            raise HTTPException(409, "Record current issues and open a correction round before worker edits")


def correction_wave(receipt):
    return "output" if receipt.final_review else "text"


def worker_ready(db, run, receipt, value):
    stage = value["execution_stage"]
    if stage in REVIEW_STAGES:
        return True
    if stage in CORRECTION_STAGES:
        from app.services.mcp_batch_workflow import assert_correction_barrier
        try:
            assert_correction_barrier(db, run, receipt.image_id)
            return True
        except HTTPException:
            return False
    return False


def worker_steps(run, receipt, value):
    args = {"run_id": run.id, "image_id": receipt.image_id,
        "worker_id": receipt.assignment["worker_id"], "assignment_token": receipt.assignment["token"]}
    if value["execution_stage"] == "configuration_changed":
        return [step("workflow_advance", {**args, "action": "release"},
            "Release the page before the coordinator accepts changed configuration.", required_inputs=["operation_id"])]
    steps = []
    for item in value["next_steps"]:
        if item["tool"] in {"workflow_advance", "task_retry", "task_cancel", "workflow_export"}:
            continue
        arguments = dict(item["arguments"])
        if item["tool"] in {"workflow_review_page", "workflow_begin_correction", "page_edit", "mask_paint"}:
            arguments.update(worker_id=args["worker_id"], assignment_token=args["assignment_token"])
        steps.append({**item, "arguments": arguments})
    if not steps:
        steps = [step("workflow_advance", {**args, "action": "release"},
            "Release the edited page for coordinator processing; only the coordinator queues model work.", required_inputs=["operation_id"])]
    return steps


def assignment_result(db, run, receipt):
    from app.services.mcp_workflow_runtime import snapshot
    from app.services.mcp_workflows import configuration
    stale = configuration(require_project(db, run.project_id)) != run.options["configuration"]
    value, _ = snapshot(db, run, receipt, stale)
    return {"run_id": run.id, "image_id": receipt.image_id, "version": value["version"],
        "expected_versions": {receipt.image_id: value["version"]}, "assignment": receipt.assignment,
        "next_steps": worker_steps(run, receipt, value)}


def advance(args):
    from app.services.mcp_workflow_runtime import snapshot
    from app.services.mcp_workflows import configuration, lock_run_project, run_row
    action = args["action"]
    if args.get("image_ids") or args.get("coordinator_token"):
        raise HTTPException(422, "Worker lease actions accept one page and worker credentials only")
    if not args.get("worker_id"):
        raise HTTPException(422, "Worker lease actions require worker_id")
    with SessionLocal() as db:
        run = run_row(db, args["run_id"])
        def save():
            lock_run_project(db, run)
            if not parallel(run):
                raise HTTPException(409, "This is a single-agent workflow; follow its existing next_steps")
            if run.options.get("superseded_by"):
                raise HTTPException(409, "Workflow was superseded; resume the newer run")
            for identifier in ([args["image_id"]] if args.get("image_id") else run.page_ids):
                if owners(db, identifier, exclude_run_id=run.id):
                    raise HTTPException(409, "Another active parallel run owns these pages")
            stale = configuration(require_project(db, run.project_id)) != run.options["configuration"]
            if stale and action != "release":
                raise HTTPException(409, "Workflow configuration changed; inspect before claiming more work")
            rows = list(db.scalars(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run.id).with_for_update()))
            by_page = {row.image_id: row for row in rows}
            if args.get("image_id") and args["image_id"] not in by_page:
                raise HTTPException(422, "Page is outside this workflow")
            if action == "claim":
                owned = next((row for row in rows if live(row.assignment) and row.assignment["worker_id"] == args["worker_id"]), None)
                if owned:
                    if args.get("image_id") and args["image_id"] != owned.image_id:
                        raise HTTPException(409, "Release the current page before claiming another")
                    response = assignment_result(db, run, owned)
                else:
                    selected = None
                    for identifier in run.page_ids:
                        row = by_page[identifier]
                        if live(row.assignment) or args.get("image_id") and identifier != args["image_id"]:
                            continue
                        value, _ = snapshot(db, run, row, stale)
                        if worker_ready(db, run, row, value):
                            selected = row
                            run.options = {**run.options, "coordination_closed": False}
                            selected.assignment = {"worker_id": args["worker_id"], "token": secrets.token_urlsafe(24),
                                "phase": value["execution_stage"], "claimed_at": utcnow().isoformat(),
                                "expires_at": (utcnow()+timedelta(seconds=LEASE_SECONDS)).isoformat()}
                            break
                    response = assignment_result(db, run, selected) if selected else {
                        "run_id": run.id, "assignment": None, "next_tool": "workflow_status",
                        "message": "No unclaimed ready page. Do not recreate the run or resume cancelled work."}
            else:
                row = by_page.get(args.get("image_id"))
                if not row or not live(row.assignment) or row.assignment["worker_id"] != args["worker_id"] or not same_token(row.assignment["token"], args.get("assignment_token", "")):
                    raise HTTPException(409, "Page assignment expired or changed; claim again")
                if action == "renew":
                    row.assignment = {**row.assignment, "expires_at": (utcnow()+timedelta(seconds=LEASE_SECONDS)).isoformat()}
                    response = assignment_result(db, run, row)
                else:
                    row.assignment = {}
                    response = {"run_id": run.id, "image_id": row.image_id, "released": True, "next_tool": "workflow_status"}
            op = db.get(MCPOperation, operation_identifier.get()) if operation_identifier.get() else None
            if op:
                op.result = {**op.result, "_coordination": response}
            return response
        return project_write(db, run.project_id, save)


def decorate_status(db, run, receipts, values, args, response):
    """Read-only coordinator/worker views; querying never extends a lease."""
    available, claimed, corrections, waiting = [], [], 0, 0
    for value in values:
        row = receipts[value["image_id"]]
        if live(row.assignment):
            claimed.append(row)
        elif worker_ready(db, run, row, value):
            available.append(value["image_id"])
        corrections += value["execution_stage"] in CORRECTION_STAGES
        waiting += value["execution_stage"] in {"waiting", "paused"}
    response["coordination"] = {"mode": "parallel", "claimable": len(available), "claimed": len(claimed),
        "waiting": waiting, "correction_pending": corrections, "lease_seconds": LEASE_SECONDS}
    if run.options.get("superseded_by"):
        response["coordination"].update(superseded_by=run.options["superseded_by"], claimable=0)
        response.update(next_steps=[], current_page_id=None)
        return response
    worker = args.get("worker_id")
    if worker:
        owned = [row for row in claimed if row.assignment["worker_id"] == worker]
        supplied = args.get("assignment_token", "")
        if owned and not same_token(owned[0].assignment["token"], supplied):
            response.update(pages=[], next_steps=[], current_page_id=None)
            response["coordination"]["credential_required"] = True
            return response
        response["pages"] = [{**value, "assignment": row.assignment,
            "next_steps": worker_steps(run, row, value)} for value in values for row in owned if value["image_id"] == row.image_id]
        response["current_page_id"] = owned[0].image_id if owned else None
        response["next_steps"] = response["pages"][0]["next_steps"] if owned else [step("workflow_advance", {
            "run_id": run.id, "action": "claim", "worker_id": worker}, "Claim one ready page atomically.", required_inputs=["operation_id"])]
        response.update(offset=0, next_offset=None)
    else:
        # Never expose another worker's assignment token to the coordinator view.
        response["coordination"]["coordinator_operation_id"] = run.options.get("coordinator_operation_id")
        response["coordination"]["claim"] = {"tool": "workflow_advance", "arguments": {"run_id": run.id, "action": "claim"}, "required_inputs": ["worker_id", "operation_id"]}
        if (response["next_steps"] and response["next_steps"][0]["tool"] == "workflow_advance"
                and response["next_steps"][0]["arguments"].get("action", "process") == "process"):
            # A held correction page must not occupy the coordinator's batch
            # while other pages are ready. Schedule only released pages.
            text_stages = {"detection", "ocr", "translation"}
            seed_ids = response["next_steps"][0]["arguments"].get("image_ids") or [response["next_steps"][0]["arguments"].get("image_id")]
            text = next((value["execution_stage"] in text_stages for value in values if value["image_id"] in seed_ids), False)
            ready = [value for value in values if not live(receipts[value["image_id"]].assignment)
                and (value["execution_stage"] in text_stages) == text
                and any(item["tool"] == "workflow_advance" for item in value["next_steps"])][:20]
            if not ready and run.options.get("batch_phase") == "output":
                ready = [value for value in values if not live(receipts[value["image_id"]].assignment)
                    and any(item["tool"] == "workflow_advance" for item in value["next_steps"])][:20]
            response["next_steps"] = [step("workflow_advance", {"run_id": run.id,
                "image_ids": [value["image_id"] for value in ready],
                "expected_versions": {value["image_id"]: value["version"] for value in ready}},
                "Queue released pages; workers retain exclusive ownership of their held pages.", required_inputs=["operation_id"])] if ready else []
        for item in response["next_steps"]:
            if item["tool"] in {"workflow_advance", "workflow_export", "task_retry", "task_cancel", "page_edit"}:
                item["required_inputs"] = list(dict.fromkeys([*item.get("required_inputs", []), "coordinator_token"]))
        if response["next_steps"] and response["next_steps"][0]["tool"] in {"page_review_plan", "workflow_begin_correction", "page_state", "workflow_review_page"}:
            response["next_steps"] = [step("workflow_get", {"role": "worker"},
                "Delegate claimable pages to client-native workers; choose concurrency in the client.")] if available else []
        elif (available and response["next_steps"] and response["next_steps"][0]["tool"] == "task_wait"
                and response["next_steps"][0]["arguments"].get("kind") == "processing"):
            response["next_steps"] = [step("workflow_get", {"role": "worker"},
                "Other pages are ready for workers; delegate them before waiting for this page's local processing.")]
        if not response["next_steps"] and claimed:
            response["coordination"]["next_instruction"] = "Wait for existing client-native workers; do not spawn replacements for held pages."
    return response

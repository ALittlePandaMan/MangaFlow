"""Compact advice and one-step queue admission, without another scheduler."""
from app.api.helpers import require_project
from app.core.database import SessionLocal
from app.models import AgentReviewIssue, FileJob, ImagePage, MCPOperation, MCPWorkflowPage, ProcessingTask
from app.services.mcp_actions import compact_task
from app.services.mcp_workflow_steps import page_steps, step
from app.services.workflow_state import current_page_version
from sqlalchemy import select


def public_steps(steps):
    result = []
    for value in steps:
        value = {**value, "arguments": dict(value["arguments"])}
        name, args = value["tool"], value["arguments"]
        if name == "images_process_image":
            value = step("workflow_advance", {"run_id": args["workflow_run_id"], "image_id": args["image_id"],
                "expected_versions": args["expected_versions"]}, "Advance necessary backend stages, then wait for the task.", required_inputs=["operation_id"])
        elif name == "regions_bulk_update_regions":
            value = step("page_edit", {"image_id": next(iter(args["expected_versions"])), "workflow_run_id": args["workflow_run_id"],
                "expected_versions": args["expected_versions"], "update": [{"region_id": item["id"], "patch": {}} for item in args["body"]]},
                "Write your translations for the listed regions, then advance.", required_inputs=["operation_id", "update[].patch.translated_text"], automatic=False)
        elif name in {"tasks_get_task", "file_jobs_get_job"}:
            value["tool"] = "task_wait"
            value["arguments"] = {"task_id": args.get("task_id", args.get("identifier")),
                "kind": "file" if name.startswith("file_jobs") else "processing", "seconds": 0}
        elif name == "tasks_retry_task":
            value["tool"] = "task_retry"
        if value["tool"] == "workflow_review_page":
            value["required_inputs"] = ["selection" if key == "region_ids" else key for key in value.get("required_inputs", [])]
        result.append(value)
    return result


def snapshot(db, run, receipt, configuration_stale):
    from app.services.mcp_workflows import ACTIVE, blockers, source_version
    page = db.get(ImagePage, receipt.image_id)
    version = current_page_version(db, receipt.image_id) if page else "deleted"
    tasks = list(db.scalars(select(ProcessingTask).where(ProcessingTask.image_id == receipt.image_id).order_by(ProcessingTask.created_at)))
    stage, steps = page_steps(db, run, receipt, page, version, tasks, configuration_stale=configuration_stale,
        source_fingerprint=source_version(db, receipt.image_id) if page else None,
        blockers=lambda **kwargs: blockers(db, run, **kwargs))
    # Only live work and the latest workflow task are relevant to the next call.
    relevant = [item for item in tasks if item.status in ACTIVE]
    linked = [item for item in tasks if item.payload.get("options", {}).get("workflow_run_id") == run.id]
    if linked and linked[-1] not in relevant:
        relevant.append(linked[-1])
    final = receipt.final_review
    return {"image_id": receipt.image_id, "version": version,
        "status": "stale" if final and final.get("version") != version else final.get("outcome", "pending"),
        "execution_stage": stage, "correction_rounds": receipt.correction_rounds, "correction_open": receipt.correction_open,
        "remaining_rounds": max(0, 2-receipt.correction_rounds),
        "tasks": [compact_task(item) for item in relevant[-5:]],
        "next_steps": public_steps(steps)}, steps


def status(args):
    from app.services.file_jobs import job_read
    from app.services.mcp_workflows import ACTIVE, blockers, configuration, delivery_version, run_row
    with SessionLocal() as db:
        run = run_row(db, args["run_id"])
        stale = configuration(require_project(db, run.project_id)) != run.options["configuration"]
        receipts = {item.image_id: item for item in db.scalars(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run.id))}
        snapshots = [snapshot(db, run, receipts[identifier], stale) for identifier in run.page_ids]
        values = [item[0] for item in snapshots]
        current = next((i for i, value in enumerate(values) if value["execution_stage"] != "ready_for_delivery"), len(values))
        from app.services.mcp_batch_runtime import advice
        batch = advice(run, snapshots)
        if batch is not None:
            current = batch["current"]
        offset, limit = args.get("offset", min(current, max(0, len(values)-1))), args.get("limit", 1)
        pages = values[offset:offset+limit]
        from app.services.mcp_coordination import decorate_status, parallel
        operations = list(db.scalars(select(MCPOperation).where(MCPOperation.workflow_run_id == run.id,
            MCPOperation.status.in_(["UNKNOWN"] if parallel(run) else ["RUNNING", "UNKNOWN"]), MCPOperation.id != args.get("operation_id", ""))
            .order_by(MCPOperation.created_at.desc()).limit(5)))
        export_ids = select(MCPOperation.result["structuredContent"]["job"]["id"].as_string()).where(MCPOperation.workflow_run_id == run.id)
        exports = list(db.scalars(select(FileJob).where(FileJob.id.in_(export_ids)).order_by(FileJob.created_at.desc()).limit(3)))
        deliverable = not stale and all(value["status"] != "pending" and not value["correction_open"]
            and value["status"] != "stale" for value in values) and not db.scalar(select(ProcessingTask.id).where(
                ProcessingTask.image_id.in_(run.page_ids), ProcessingTask.status.in_(ACTIVE)).limit(1))
        delivery = delivery_version(db, run, list(receipts.values())) if deliverable else None
        matching_exports = [job for job in exports if delivery and job.result.get("workflow_delivery_version") == delivery]
        current_exports = [job for job in matching_exports if job.status == "COMPLETED"]
        next_steps = values[current]["next_steps"] if current < len(values) else []
        if batch is not None:
            next_steps = batch["next_steps"]
        pending_exports = [job for job in exports if job.status in {"QUEUED", "RUNNING", "CANCELLING"}]
        if pending_exports or current_exports:
            job = (pending_exports or current_exports)[0]
            next_steps = [step("task_wait", {"task_id": job.id, "kind": "file", "seconds": 20 if pending_exports else 0},
                "Read the existing export and protected output/report paths.", automatic=job.status not in {"FAILED", "CANCELLED"})]
        elif deliverable and current == len(values):
            terminal = next((job for job in matching_exports if job.status in {"FAILED", "CANCELLED"}), None)
            kind = terminal.kind if terminal and terminal.kind in {"cbz", "translated"} else "cbz"
            next_steps = [step("workflow_export", {"run_id": run.id, "kind": kind},
                ("The previous export failed. Inspect its error in exports/task_wait and resolve the cause before submitting a NEW operation_id."
                    if terminal and terminal.status == "FAILED" else
                 "The previous export was cancelled. Submit a NEW operation_id only if explicitly asked to export again."
                    if terminal else "Export all reviewed pages."),
                required_inputs=["operation_id"], automatic=terminal is None)]
        if operations:
            next_steps = [step("operation_status", {"operation_id": item.id},
                "Inspect/reconcile this accepted operation before further writes; accepting unknown current state requires inspecting all frozen pages.") for item in operations]
        configuration_change = None
        if stale and not operations:
            from app.services.mcp_configuration import recovery_steps
            configuration_change, next_steps = recovery_steps(db, run, receipts, values)
        if args.get("include_details"):
            from app.services.quality import evaluate_page
            from sqlalchemy import func
            for value in pages:
                page = db.get(ImagePage, value["image_id"])
                if page:
                    hints = evaluate_page(page, persist=False)
                    value["quality_hints"] = [{key: getattr(item, key) for key in ("region_key", "code", "message", "severity")} for item in hints[:20]]
                    value["quality_hint_count"] = len(hints)
                query = select(AgentReviewIssue).where(AgentReviewIssue.workflow_run_id == run.id, AgentReviewIssue.image_id == value["image_id"])
                value["issue_count"] = db.scalar(select(func.count()).select_from(query.subquery()))
                value["issues"] = [{"issue_id": item.id, "region_id": item.region_id, "category": item.category,
                    "message": item.message, "status": item.status, "stale": item.page_version != value["version"]}
                    for item in db.scalars(query.order_by(AgentReviewIssue.created_at, AgentReviewIssue.id).limit(20))]
                if max(value.get("quality_hint_count", 0), value["issue_count"]) > 20:
                    value["details_next_step"] = step("page_state", {"image_id": value["image_id"], "run_id": run.id,
                        "include_details": True, "details_offset": 20, "expected_version": value["version"]}, "Read the remaining issues/hints.")
        counts = {key: sum(value["status"] == key for value in values) for key in ("passed", "issues", "unverified", "stale", "pending")}
        response = {"run_id": run.id, "project_id": run.project_id, "workflow_version": run.workflow_version,
            "page_count": len(values), "counts": counts, "current_page_id": values[current]["image_id"] if current < len(values) else None,
            "pages": pages, "offset": offset, "next_offset": offset+len(pages) if offset+len(pages) < len(values) else None,
            "configuration_stale": stale, "blockers": [{"kind": item["kind"], "status": item["status"]} for item in blockers(db, run)],
            "next_steps": next_steps, **({"batch": batch["summary"]} if batch is not None else {}),
            **({"configuration_change": configuration_change} if configuration_change else {}),
            "exports": [{**compact_task(job_read(job), "file"), "current": job in current_exports} for job in exports]}
        return decorate_status(db, run, receipts, values, args, response) if parallel(run) else response


def advance(args):
    from app.api.images import process_image
    from app.schemas.domain import ProcessRequest
    from app.services.mcp_context import check_page_version, workflow_action, workflow_run_id
    from app.services.mcp_coordination import advance as coordinate
    from app.services.mcp_coordination import coordinator
    from app.services.mcp_workflows import configuration, page_row, run_row
    if args.get("action") == "accept_configuration":
        from app.services.mcp_configuration import accept
        return accept(args)
    if args.get("action", "process") != "process":
        return coordinate(args)
    with SessionLocal() as db:
        coordinator(run_row(db, args["run_id"]))
    state = status(args)
    if state["next_steps"] and state["next_steps"][0]["tool"] == "operation_status":
        return {"run_id": args["run_id"], "next_steps": state["next_steps"]}
    if "image_ids" in args:
        from app.services.mcp_batch_runtime import advance_batch
        return advance_batch(args)
    identifier = args.get("image_id") or state["current_page_id"]
    if not identifier:
        return {"run_id": args["run_id"], "next_steps": state["next_steps"]}
    with SessionLocal() as db:
        run = run_row(db, args["run_id"])
        receipt = page_row(db, run.id, identifier)
        check_page_version(db, identifier)
        value, steps = snapshot(db, run, receipt, configuration(require_project(db, run.project_id)) != run.options["configuration"])
        process = next((item for item in steps if item["tool"] == "images_process_image"), None)
        if not process:
            return {"run_id": run.id, "page": value, "next_steps": value["next_steps"]}
        from app.services.mcp_batch_workflow import enabled
        if enabled(run):
            from app.services.mcp_batch_runtime import advance_batch
            response = advance_batch({**args, "image_ids": [identifier]})
            if response.get("tasks"):
                return {**response, "image_id": identifier, "task": response["tasks"][0]}
            return response
        token, action = workflow_run_id.set(run.id), workflow_action.set("workflow_advance")
        try:
            task = process_image(identifier, ProcessRequest(**process["arguments"]["body"]), db)
            return {"run_id": run.id, "image_id": identifier, "task": compact_task(task), "next_tool": "task_wait"}
        finally:
            workflow_action.reset(action)
            workflow_run_id.reset(token)

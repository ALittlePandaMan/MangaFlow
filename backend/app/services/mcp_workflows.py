"""Durable agent checkpoints; inference continues to use the existing queues."""
from copy import deepcopy

from app.api.helpers import require_page, require_project
from app.core.database import SessionLocal
from app.core.transactions import project_write
from app.models import (
    AgentReviewIssue,
    FileJob,
    MCPWorkflowPage,
    MCPWorkflowRun,
    ProcessingTask,
    TextRegion,
)
from app.models.entities import utcnow
from app.services.mcp_context import check_page_version
from app.services.mcp_review_plan import (
    READABLE_SCALE,
    REQUIRED_COVERAGE,
    clean_preview_path,
    evidence_views,
    load_evidence,
    readable_rectangles,
    requirements,
)
from app.services.mcp_review_plan import (
    covered_area as _covered_area,
)
from app.services.mcp_workflow_definitions import CHECKS, PREFLIGHT_CHECKS, workflow_get
from app.services.workflow_state import current_page_version, fingerprint, record
from fastapi import HTTPException
from sqlalchemy import select, update

ACTIVE = ("QUEUED", "RUNNING", "CANCELLING", "PAUSED")


def run_row(db, identifier):
    row = db.get(MCPWorkflowRun, identifier)
    if row is None:
        raise HTTPException(404, "Workflow run not found")
    return row


def page_row(db, run_id, image_id, *, lock=False):
    query = select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run_id, MCPWorkflowPage.image_id == image_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(422, "Page is outside the frozen workflow scope")
    return row


def lock_run_project(db, run):
    from app.models import Project
    # Use the same publication lock as page edits; no inference happens here.
    db.scalar(select(Project).where(Project.id == run.project_id).with_for_update())
    db.execute(update(MCPWorkflowRun).where(MCPWorkflowRun.id == run.id).values(updated_at=MCPWorkflowRun.updated_at))


def source_version(db, image_id):
    rows = db.scalars(select(TextRegion).where(TextRegion.image_id == image_id).order_by(TextRegion.id))
    return fingerprint([{key: getattr(row, key) for key in ("id", "bbox", "polygon", "source_text", "locked")} for row in rows])


def cleared_manual_mask(region, page):
    """Only a verified empty, edited mask may restore excluded artwork."""
    generation = (region.layout_data or {}).get("mask_generation", {})
    if not (region.pixel_mask_path and generation.get("method") == "manual_mask" and generation.get("repair_pending")):
        return False
    from app.storage import get_storage
    from PIL import Image
    try:
        with Image.open(get_storage().absolute(region.pixel_mask_path)) as mask:
            if mask.size != (page.width, page.height):
                raise ValueError("Invalid dimensions")
            return mask.convert("L").getbbox() is None
    except (OSError, ValueError) as exc:
        raise HTTPException(409, "Cannot verify cleared mask; inspect or replace the saved mask before restoring artwork") from exc


def configuration(project):
    return {key: deepcopy(getattr(project, key)) for key in ("source_language", "target_language", "settings", "translation_context")}


def blockers(db, run, *, required=None):
    from app.services.infra.model_provisioning import setup_status_report
    report = setup_status_report(db)
    if required is None:
        required = {"detection", "ocr", "inpainting", "rendering", "font"}
        if run.options["translation_mode"] == "configured":
            required.add("translation")
        if run.workflow_id != "translate_and_review":
            required = {"rendering", "font"}
    return [stage for stage in report["stages"] if stage["kind"] in required and stage["status"] != "ready"]


def start(args):
    from app.models import ImagePage, Project
    definition = workflow_get(args.get("workflow_id", "translate_and_review"))
    with SessionLocal() as db:
        project = require_project(db, args["project_id"])
        def save():
            project = db.scalar(select(Project).where(Project.id == args["project_id"]).with_for_update())
            rows = list(db.scalars(select(ImagePage).where(ImagePage.project_id == project.id).order_by(ImagePage.order_index, ImagePage.id)))
            selected = set(args.get("image_ids") or [page.id for page in rows])
            pages = [page for page in rows if page.id in selected]
            if not pages or len(pages) != len(selected):
                raise HTTPException(422, "Import pages first; all image_ids must belong to the project")
            regions = list(db.scalars(select(TextRegion).where(TextRegion.image_id.in_(selected))))
            from app.services.mcp_coordination import owners, token_hash
            if any(owners(db, identifier) for identifier in selected):
                raise HTTPException(409, "Pages already belong to an active parallel workflow; resume its run_id")
            import secrets
            coordinator_token = secrets.token_urlsafe(24) if args.get("coordination_mode") == "parallel" else None
            if args.get("region_ids") and not set(args["region_ids"]) <= {row.id for row in regions}:
                raise HTTPException(422, "region_ids must belong to selected pages")
            run = MCPWorkflowRun(project_id=project.id, workflow_id=definition["id"], workflow_version=definition["version"],
                page_ids=[page.id for page in pages], options={"configuration": configuration(project),
                "translation_mode": args.get("translation_mode", "configured"), "region_ids": args.get("region_ids", []),
                "max_correction_rounds": 2, "max_task_retries": 2, "preserve_art_text": True,
                "execution_mode": "batch_text_review", "batch_phase": "text",
                "coordination_mode": args.get("coordination_mode", "single"),
                **({"coordinator_hash": token_hash(coordinator_token),
                    "coordinator_operation_id": args["operation_id"]} if coordinator_token else {})})
            db.add(run)
            db.flush()
            # Finished leases must never resurrect when a newer run edits the
            # same pixels and thereby makes an older review version stale.
            from app.services.mcp_coordination import active_run, parallel
            for previous in db.scalars(select(MCPWorkflowRun).where(MCPWorkflowRun.project_id == project.id, MCPWorkflowRun.id != run.id)):
                if parallel(previous) and selected.intersection(previous.page_ids) and not active_run(db, previous):
                    previous.options = {**previous.options, "superseded_by": run.id}
            for page in pages:
                db.add(MCPWorkflowPage(run_id=run.id, image_id=page.id))
            response = {"run_id": run.id, "workflow_id": run.workflow_id, "workflow_version": run.workflow_version,
                "page_count": len(run.page_ids), "first_page_id": run.page_ids[0], "translation_mode": run.options["translation_mode"],
                "blockers": [{"kind": item["kind"], "status": item["status"]} for item in blockers(db, run)],
                "next_tool": "workflow_status", **({"coordinator_token": coordinator_token} if coordinator_token else {})}
            from app.models import MCPOperation
            op = db.get(MCPOperation, args["operation_id"])
            if op:
                op.result = {**op.result, "_coordination": response}
            return response
        return project_write(db, project.id, save)


def _quiet(db, image_id):
    if db.scalar(select(ProcessingTask.id).where(ProcessingTask.image_id == image_id, ProcessingTask.status.in_(ACTIVE)).limit(1)):
        raise HTTPException(409, "Page has unfinished processing; wait or explicitly cancel before reviewing/editing")


def guard_page(db, run_id, image_id, *, mutation=True):
    run = run_row(db, run_id)
    page = require_page(db, image_id)
    row = page_row(db, run.id, image_id, lock=mutation)
    if configuration(require_project(db, run.project_id)) != run.options["configuration"]:
        raise HTTPException(409, "Project languages/context/settings changed; inspect before starting a new workflow")
    if mutation and row.final_review and not row.correction_open:
        raise HTTPException(409, "Record a current issues review and call workflow_begin_correction before further edits")
    from app.services.mcp_batch_workflow import assert_correction_barrier, enabled
    from app.services.mcp_context import workflow_action
    preparing_agent_text = (run.options["translation_mode"] == "agent" and workflow_action.get() == "page_edit"
        and not row.preflight and not row.final_review)
    if mutation and enabled(run) and workflow_action.get() not in {"workflow_advance", "task_retry"} and not preparing_agent_text:
        assert_correction_barrier(db, run, image_id)
        if row.preflight and not row.correction_open:
            raise HTTPException(409, "Open a correction round before changing a reviewed text page")
    if page.project_id != run.project_id:
        raise HTTPException(422, "Page no longer belongs to workflow project")
    return run, row


def validate_call(args, entry, name):
    """Reject unrelated administrative writes and forged/out-of-scope region IDs."""
    from app.services.mcp_context import expected_versions
    with SessionLocal() as db:
        run = run_row(db, args["workflow_run_id"])
        if args.get("project_id") and args["project_id"] != run.project_id:
            raise HTTPException(422, "Project is outside workflow scope")
        if name.startswith("workflow_"):
            raise HTTPException(422, "Use run_id for workflow tools, not workflow_run_id")
        if entry and not entry["write"]:
            return
        if entry and not (name.startswith(("regions_", "images_", "tasks_"))):
            raise HTTPException(422, "Use workflow_run_id only for page, region, task or review operations")
        identifiers = set(expected_versions.get() or {})
        if args.get("image_id"):
            identifiers.add(args["image_id"])
        region_ids = set()
        if args.get("region_id"):
            region_ids.add(args["region_id"])
        body = args.get("body")
        if isinstance(body, list):
            region_ids.update(item["id"] if isinstance(item, dict) else item for item in body)
        if isinstance(body, dict):
            identifiers.update(body.get("image_ids") or [])
            region_ids.update(body.get("options", {}).get("region_ids") or [])
        for identifier in region_ids:
            region = db.get(TextRegion, identifier)
            if not region:
                raise HTTPException(404, "Region not found")
            identifiers.add(region.image_id)
            if name != "review_record" and region.locked:
                raise HTTPException(409, "Official workflows preserve locked regions")
            if run.options.get("region_ids") and identifier not in run.options["region_ids"] and (region.layout_data or {}).get("workflow_created_by") != run.id:
                raise HTTPException(422, "Region is outside frozen local repair scope")
        if args.get("task_id"):
            task = db.get(ProcessingTask, args["task_id"])
            if not task or task.project_id != run.project_id:
                raise HTTPException(422, "Task is outside workflow scope")
            identifiers.add(task.image_id)
            if name.endswith("resume_task") or name.endswith("retry_task") and (task.status != "FAILED" or args.get("allow_unknown_cloud_retry")):
                raise HTTPException(409, "Do not automatically resume cancelled/paused or unknown paid work")
        if not identifiers:
            raise HTTPException(422, "Workflow mutations must identify affected pages")
        if not identifiers <= set(run.page_ids):
            raise HTTPException(422, "Mutation is outside the frozen workflow pages")
        for identifier in identifiers:
            guard_page(db, run.id, identifier, mutation=False)


def prepare_task(db, task, run_id, action):
    """Called inside the existing atomic queue-admission transaction."""
    from app.services.workflow_state import requested_stages
    run, receipt = guard_page(db, run_id, task.image_id)
    if action == "workflow_advance":
        # Queue admission already holds the project/page locks. A second Agent
        # may have planned from the same unchanged content version; reject it
        # here rather than admitting a duplicate of newly queued work.
        active = db.scalar(select(ProcessingTask.id).where(ProcessingTask.image_id == task.image_id,
            ProcessingTask.id != task.id, ProcessingTask.status.in_(ACTIVE)).limit(1))
        if active:
            raise HTTPException(409, {"message": "Page already has unfinished processing; wait before advancing", "task_id": active})
    payload = deepcopy(task.payload)
    options = payload.setdefault("options", {})
    if action.endswith("retry_task") or action == "task_retry":
        count = options.get("_workflow_retries", 0)
        if count >= 2 or options.get("_workflow_previous_status") != "FAILED":
            raise HTTPException(409, "Only explicit FAILED tasks can be retried automatically, at most twice")
        options["_workflow_retries"] = count + 1
    stages = requested_stages(payload)
    if action in {"regions_delete_region", "regions_bulk_delete_regions", "regions_merge_region_list", "page_edit"} and options.get("_editor_history_entry_id"):
        # Existing delete/merge endpoints repair a structural edit using retained
        # masks. This is restoration, not admission of a new erasure allowlist.
        options["workflow_run_id"] = run_id
        task.payload = payload
        return
    if options.get("repair_geometry", "source") != "source":
        raise HTTPException(422, "Official workflow repairs use source geometry; translation frames must not enlarge erasure")
    if options.get("rebuild_clean"):
        raise HTTPException(422, "Use local repair; structural endpoints manage full clean reconstruction when necessary")
    scope = {task.region_id} if task.region_id else set(options.get("region_ids", []))
    rows = list(db.scalars(select(TextRegion).where(TextRegion.image_id == task.image_id)))
    available = {row.id for row in rows}
    if scope - available:
        raise HTTPException(422, "Selected regions do not belong to the task page")
    if any(row.locked for row in rows if row.id in scope):
        raise HTTPException(409, "Official workflows cannot override locked regions")
    if not set(options.get("retranslate_region_ids", [])) <= scope:
        raise HTTPException(422, "Retranslation requires explicit IDs inside the selected region scope")
    if not set(options.get("reocr_region_ids", [])) <= scope:
        raise HTTPException(422, "OCR refresh requires explicit IDs inside the selected region scope")
    limited = set(run.options.get("region_ids", []))
    descendants = {row.id for row in rows if (row.layout_data or {}).get("workflow_created_by") == run.id}
    if limited and (not scope or not scope <= limited | descendants) and stages != ("rendering",):
        raise HTTPException(422, "Local repair requires explicit regions inside the frozen region scope")
    from app.services.mcp_batch_workflow import enabled, preflight_current
    text_only = (enabled(run) and options.get("_workflow_text_only") and payload.get("end_stage") in {"translation", "ocr"}
        and not set(stages) & {"mask", "inpainting", "rendering"})
    if any(stage in stages for stage in ("translation", "mask", "inpainting")) and not text_only:
        before = receipt.preflight
        if not preflight_current(db, run, receipt):
            raise HTTPException(409, "Inspect current source geometry/OCR and submit preflight before translation/erasure")
        if enabled(run) and before.get("outcome") != "passed":
            raise HTTPException(409, "Deferred/unresolved text review does not authorize erasure")
        allowed = set(before.get("region_ids", []))
        if stages == ("inpainting", "rendering"):
            # Clearing an excluded region restores original pixels; it must
            # never authorize erasing it again or regenerating its mask.
            page = require_page(db, task.image_id)
            allowed.update(row.id for row in rows if row.id in scope and cleared_manual_mask(row, page))
        if not scope or not scope <= allowed:
            raise HTTPException(422, "Use an explicit nonempty subset of preflight region_ids; empty means skip processing")
        if any(row.locked for row in rows if row.id in scope):
            raise HTTPException(409, "Official workflows cannot override locked regions")
        if run.options["translation_mode"] == "agent" and "translation" in stages:
            raise HTTPException(422, "Agent translation mode: write translated_text then start at mask, not translation")
    if "detection" in stages and (rows or limited):
        raise HTTPException(409, "Official workflows do not redetect existing/manual regions; edit locally")
    required = set(stages) & {"detection", "ocr", "translation", "inpainting", "rendering"}
    if "rendering" in stages:
        required.add("font")
    if payload.get("start_stage") == "translation" and all(row.source_text.strip() for row in rows if row.id in scope):
        required.discard("ocr")
    missing = blockers(db, run, required=required)
    if missing:
        raise HTTPException(409, {"message": "Required configuration is unavailable", "blockers": missing})
    options["workflow_run_id"] = run_id
    task.payload = payload


def _evidence(db, args, version, page):
    evidence = load_evidence(db, args.get("evidence_ids", []), version, page)
    if args["outcome"] == "unverified":
        return evidence
    _, frame, readable_kinds = requirements(args["phase"])
    if args["phase"] == "preflight" and not any(value["kind"] == "annotated" and value.get("frame") == frame
            and value["crop"] == [0, 0, page.width, page.height] for value in evidence):
        raise HTTPException(422, f"Review requires full-page annotated {frame} frames")
    if args["phase"] == "final" and not clean_preview_path(page):
        raise HTTPException(409, "Clean background is unavailable/stale; complete repairs before final review")
    for kind in readable_kinds:
        matching = evidence_views(evidence, kind)
        if not any(value["crop"] == [0, 0, page.width, page.height] for value in matching):
            raise HTTPException(422, "Review must include full-page original/rendered previews to find missed text")
        # When a full preview is heavily reduced, require readable tiles covering the page.
        rectangles = readable_rectangles(evidence, kind)
        if _covered_area(rectangles) < page.width * page.height * REQUIRED_COVERAGE:
            raise HTTPException(422, f"Preview is too reduced; call page_review_plan for readable tiles (scale <={READABLE_SCALE} original pixels/display pixel)")
    return evidence


def review_page(args):
    from app.services.inpainting import mask_is_empty
    from app.storage import get_storage
    with SessionLocal() as db:
        run = run_row(db, args["run_id"])
        def save():
            lock_run_project(db, run)
            guard_page(db, run.id, args["image_id"], mutation=False)
            page = require_page(db, args["image_id"])
            receipt = page_row(db, run.id, page.id, lock=True)
            version = check_page_version(db, page.id)
            _quiet(db, page.id)
            from app.services.mcp_batch_workflow import enabled, text_version
            batch = enabled(run)
            expected = ((*PREFLIGHT_CHECKS, "translation") if batch else PREFLIGHT_CHECKS) if args["phase"] == "preflight" else CHECKS
            checks = args["checks"]
            if set(checks) != set(expected):
                raise HTTPException(422, f"Supply every check exactly once: {list(expected)}")
            if args["outcome"] == "passed" and any(value["status"] != "pass" for value in checks.values()):
                raise HTTPException(422, "Passed review requires all checks to pass")
            evidence = _evidence(db, args, version, page)
            from app.services.mcp_page_edit import resolve_regions
            seen_issues = set()
            for item in args.get("issues", []):
                issue = db.get(AgentReviewIssue, item["issue_id"]) if item.get("issue_id") else None
                if item.get("issue_id") and (not issue or issue.image_id != page.id or issue.workflow_run_id != run.id):
                    raise HTTPException(422, "Issue does not belong to this workflow page")
                if item.get("issue_id") in seen_issues:
                    raise HTTPException(422, "Duplicate issue update")
                if item.get("issue_id"):
                    seen_issues.add(item["issue_id"])
                if issue is None:
                    issue = AgentReviewIssue(project_id=page.project_id, image_id=page.id, workflow_run_id=run.id)
                    db.add(issue)
                if item.get("region_id"):
                    issue.region_id = resolve_regions(db, page.id, [item["region_id"]])[0].id
                for key in ("category", "message", "status"):
                    setattr(issue, key, item[key])
                issue.page_version = version
            db.flush()
            issues = list(db.scalars(select(AgentReviewIssue).where(AgentReviewIssue.workflow_run_id == run.id, AgentReviewIssue.image_id == page.id)))
            if args["outcome"] == "passed" and any(issue.status in {"open", "uncertain", "unverified"} for issue in issues):
                raise HTTPException(409, "Resolve or explain all recorded issues before marking passed")
            value = {"version": version, "outcome": args["outcome"], "checks": checks, "evidence": evidence,
                "note": args["note"], "reviewed_at": utcnow().isoformat(), "round": receipt.correction_rounds}
            if receipt.assignment:
                from datetime import datetime
                value.update(worker_id=receipt.assignment["worker_id"],
                    claim_to_review_seconds=max(0, (utcnow()-datetime.fromisoformat(receipt.assignment["claimed_at"])).total_seconds()))
                for issue in issues:
                    if issue.page_version == version:
                        issue.author = receipt.assignment["worker_id"]
            if args["phase"] == "preflight":
                rows = list(db.scalars(select(TextRegion).where(TextRegion.image_id == page.id)))
                selection = args.get("selection")
                deferred = batch and args.get("disposition") == "defer" and args["outcome"] != "passed"
                if selection is None and deferred:
                    selection = {"mode": "regions", "region_ids": []}
                if selection is None:
                    raise HTTPException(422, "Preflight selection is required; explicitly select regions or all_eligible")
                if selection["mode"] == "regions":
                    if "region_ids" not in selection or "exclude" in selection:
                        raise HTTPException(422, "regions selection requires region_ids and no exclude")
                    allowed = {row.id for row in resolve_regions(db, page.id, selection["region_ids"])}
                else:
                    if "region_ids" in selection:
                        raise HTTPException(422, "all_eligible uses exclude, not region_ids")
                    excluded = {row.id for row in resolve_regions(db, page.id, selection.get("exclude", []))}
                    limited = set(run.options.get("region_ids") or [])
                    allowed = {row.id for row in rows if not row.locked and row.region_type != "sfx" and row.id not in excluded
                        and (not limited or row.id in limited or (row.layout_data or {}).get("workflow_created_by") == run.id)}
                if deferred:
                    allowed = set()  # A problem receipt cannot grant erasure permission.
                if not allowed <= {row.id for row in rows if not row.locked}:
                    raise HTTPException(422, "Preflight allowlist must contain existing unlocked text regions")
                limited = set(run.options.get("region_ids", []))
                descendants = {row.id for row in rows if (row.layout_data or {}).get("workflow_created_by") == run.id}
                if limited and not allowed <= limited | descendants:
                    raise HTTPException(422, "Preflight exceeds frozen region scope")
                if batch and args["outcome"] == "passed" and any(
                        (row.layout_data or {}).get("ocr_geometry_stale") for row in rows if row.id in allowed):
                    raise HTTPException(409, "Source OCR is stale; advance local OCR or explicitly confirm source_text before passing preflight")
                preserved = deepcopy(args.get("preserved", []))
                for item in preserved:
                    if item.get("region_id"):
                        item["region_id"] = resolve_regions(db, page.id, [item["region_id"]])[0].id
                    if not item.get("region_id") and not item.get("crop"):
                        raise HTTPException(422, "Preserved artwork needs region_id or crop and a reason")
                    if item.get("region_id") and item["region_id"] not in {row.id for row in rows}:
                        raise HTTPException(422, "Preserved region does not belong to page")
                    if item.get("crop"):
                        import math
                        x, y, width, height = item["crop"]
                        if not all(math.isfinite(value) for value in item["crop"]) or min(x, y) < 0 or min(width, height) <= 0 or x+width > page.width or y+height > page.height:
                            raise HTTPException(422, "Preserved crop must use valid original-page pixel bounds")
                preserved_ids = {item["region_id"] for item in preserved if item.get("region_id")}
                if preserved_ids & allowed:
                    raise HTTPException(422, "Preserved artwork cannot also be in the processing allowlist")
                for row in rows:
                    if row.id in allowed and row.region_type == "sfx":
                        raise HTTPException(422, "Official workflow preserves SFX artwork; exclude and record it")
                    if row.region_type == "sfx" and row.id not in preserved_ids and not deferred:
                        raise HTTPException(422, "Record preserved SFX artwork and its reason")
                    if row.id in preserved_ids and row.pixel_mask_path and not mask_is_empty(get_storage().absolute(row.pixel_mask_path)):
                        raise HTTPException(409, "Excluded text still has an erasure mask; clear and restore its artwork before preflight")
                    if row.id in preserved_ids and row.visible and row.translated_text.strip():
                        raise HTTPException(409, "Preserved artwork still has a visible translation; clear or hide that overlay as well")
                value.update(source_version=source_version(db, page.id), region_ids=sorted(allowed), preserved=preserved)
                if batch:
                    if args.get("disposition") == "defer" and args["outcome"] == "passed":
                        raise HTTPException(422, "A deferred text review cannot be marked passed")
                    value.update(text_version=text_version(db, page.id), disposition=args.get("disposition", "fix"))
                    if receipt.correction_open:
                        # Close a text correction only after current evidence
                        # has been checked. Final correction closes at final.
                        if not receipt.final_review:
                            receipt.correction_open = False
                receipt.preflight = value
            else:
                if args["outcome"] == "issues" and args.get("disposition") not in ({"fix", "deliver_with_issues", "defer"} if batch else {"fix", "deliver_with_issues"}):
                    raise HTTPException(422, "Final issues require disposition=fix or deliver_with_issues")
                if args["outcome"] == "issues" and args.get("disposition") == "fix" and receipt.correction_rounds >= 2:
                    raise HTTPException(409, "Correction budget exhausted; record deliver_with_issues")
                value["disposition"] = args.get("disposition", "deliver_with_issues")
                receipt.final_review = value
                receipt.correction_open = False
            from app.services.mcp_coordination import parallel
            if parallel(run) and not receipt.correction_open:
                receipt.assignment = {}
            db.flush()
            return {"image_id": page.id, "version": version, "phase": args["phase"], "outcome": args["outcome"],
                "issue_ids": [item.id for item in issues], "correction_rounds": receipt.correction_rounds,
                "remaining_rounds": max(0, 2-receipt.correction_rounds)}
        return project_write(db, run.project_id, save)


def begin_correction(args):
    with SessionLocal() as db:
        run = run_row(db, args["run_id"])
        def save():
            lock_run_project(db, run)
            guard_page(db, run.id, args["image_id"], mutation=False)
            row = page_row(db, run.id, args["image_id"], lock=True)
            version = check_page_version(db, row.image_id)
            _quiet(db, row.image_id)
            from app.services.mcp_batch_workflow import (
                assert_correction_barrier,
                correction_phase,
                enabled,
                preflight_current,
            )
            if enabled(run):
                assert_correction_barrier(db, run, row.image_id)
                from app.services.mcp_coordination import correction_wave
                correction_phase(run, wave=correction_wave(row))
            if row.correction_open:
                raise HTTPException(409, "Correction round already open; finish and re-review it first")
            final_current = row.final_review and row.final_review.get("version") == version and row.final_review.get("outcome") == "issues"
            recovery = (row.final_review and row.final_review.get("version") != version
                and row.preflight.get("version") == version and row.preflight.get("outcome") == "issues"
                and row.preflight.get("source_version") == source_version(db, row.image_id))
            text_issues = enabled(run) and preflight_current(db, run, row) and row.preflight.get("outcome") in {"issues", "unverified"}
            if not final_current and not recovery and not text_issues:
                raise HTTPException(409, "A current issues review is required; after an external edit, inspect original/source annotations and submit a current preflight issues review first")
            if row.correction_rounds >= 2:
                raise HTTPException(409, "Correction budget exhausted; report unresolved issues and export")
            row.correction_rounds += 1
            row.correction_open = True
            return {"image_id": row.image_id, "round": row.correction_rounds, "remaining_rounds": 2-row.correction_rounds}
        return project_write(db, run.project_id, save)


def delivery_version(db, run, receipts):
    """Bind an artifact to reviews as well as pixels; reports can change alone."""
    by_page = {item.image_id: item for item in receipts}
    issues = db.scalars(select(AgentReviewIssue).where(AgentReviewIssue.workflow_run_id == run.id).order_by(AgentReviewIssue.id))
    failures = db.scalars(select(ProcessingTask).where(ProcessingTask.image_id.in_(run.page_ids),
        ProcessingTask.status.in_(["FAILED", "CANCELLED"])).order_by(ProcessingTask.id))
    return fingerprint({"run_id": run.id, "configuration": run.options["configuration"],
        "pages": [{"image_id": identifier, "review": by_page[identifier].final_review,
            "rounds": by_page[identifier].correction_rounds,
            "preserved": by_page[identifier].preflight.get("preserved", [])} for identifier in run.page_ids],
        "issues": [record(item) for item in issues],
        "failed_tasks": [{"id": item.id, "status": item.status, "message": item.error_message} for item in failures]})


def status(args):
    from app.services.mcp_workflow_runtime import status as compact_status
    return compact_status(args)


def export_run(args):
    from app.services.file_jobs import job_read
    with SessionLocal() as db:
        run = run_row(db, args["run_id"])
        from app.services.mcp_coordination import coordinator
        coordinator(run)
        def save():
            lock_run_project(db, run)
            pages, versions = [], {}
            for identifier in run.page_ids:
                page = require_page(db, identifier)
                _quiet(db, identifier)
                row = page_row(db, run.id, identifier, lock=True)
                from app.services.mcp_coordination import live
                if live(row.assignment):
                    raise HTTPException(409, "A worker still owns this page; finish/release it before export")
                version = current_page_version(db, identifier)
                if row.correction_open or not row.final_review or row.final_review.get("version") != version:
                    raise HTTPException(409, {"message": "Page requires a final current-version review or explicit unverified result", "image_id": identifier})
                from app.services.mcp_batch_workflow import enabled
                if enabled(run) and row.final_review.get("outcome") == "issues" and row.final_review.get("disposition") != "deliver_with_issues":
                    raise HTTPException(409, "Resolve deferred final issues or explicitly choose deliver_with_issues before export")
                if row.final_review["outcome"] == "passed":
                    from app.services.file_export import current_render
                    regions = db.scalars(select(TextRegion).where(TextRegion.image_id == identifier))
                    if not current_render({"page": record(page), "regions": {region.id: record(region) for region in regions}}):
                        raise HTTPException(409, {"message": "Reviewed rendering is stale; render then review again", "image_id": identifier})
                versions[identifier] = version
                issues = list(db.scalars(select(AgentReviewIssue).where(AgentReviewIssue.workflow_run_id == run.id, AgentReviewIssue.image_id == identifier)))
                failed = list(db.scalars(select(ProcessingTask).where(ProcessingTask.image_id == identifier, ProcessingTask.status.in_(["FAILED", "CANCELLED"]))))
                pages.append({"image_id": identifier, "filename": page.filename, "version": version,
                    "review": row.final_review, "correction_rounds": row.correction_rounds,
                    "preserved": row.preflight.get("preserved", []), "issues": [record(issue) for issue in issues],
                    "failed_tasks": [{"id": task.id, "status": task.status, "message": task.error_message} for task in failed]})
            report = {"run_id": run.id, "workflow_id": run.workflow_id, "workflow_version": run.workflow_version,
                "created_at": utcnow().isoformat(), "pages": pages,
                "fully_verified": all(item["review"]["outcome"] == "passed" for item in pages)}
            delivery = delivery_version(db, run, list(db.scalars(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run.id))))
            job = FileJob(project_id=run.project_id, kind=args.get("kind", "cbz"), filename=f"MangaFlow · {args.get('kind', 'cbz').upper()}",
                result={"submitted_via": "mcp", "workflow_delivery_version": delivery, "mcp_operation_id": args["operation_id"]},
                snapshot={"workflow_contract": {"run_id": run.id, "page_ids": run.page_ids, "versions": versions,
                    "configuration": run.options["configuration"], "report": report, "delivery_version": delivery}})
            db.add(job)
            from app.services.mcp_coordination import parallel
            if parallel(run):
                run.options = {**run.options, "coordination_closed": True}
            db.flush()
            return {"job": job_read(job), "next_tool": "task_wait", "report_downloads": {
                kind: f"/mangaflow/reports/{job.id}/{kind}" for kind in ("json", "markdown")}}
        return project_write(db, run.project_id, save)


def validate_export_snapshot(raw, contract, db):
    from app.services.file_export import current_render
    run = run_row(db, contract["run_id"])
    receipts = list(db.scalars(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run.id).execution_options(populate_existing=True)))
    if (not contract.get("delivery_version") or contract["delivery_version"] != delivery_version(db, run, receipts)
            or any(row.correction_open for row in receipts)):
        raise ValueError("Workflow review records changed before export snapshot; inspect current reviews and submit a new export")
    if {key: raw["project"][key] for key in contract["configuration"]} != contract["configuration"]:
        raise ValueError("Workflow project configuration changed before export; inspect and review again")
    passed = {item["image_id"] for item in contract["report"]["pages"] if item["review"]["outcome"] == "passed"}
    from app.services.workflow_state import page_version
    for state in raw["pages"]:
        identifier = state["page"]["id"]
        if page_version(state) != contract["versions"][identifier]:
            raise ValueError(f"Workflow review is stale for page {identifier}; fetch fresh images and review again")
        if identifier in passed and not current_render(state):
            raise ValueError(f"Reviewed rendering unavailable for page {identifier}; render and review again")


def write_export_reports(identifier, contract, result):
    import json

    from app.services.file_jobs import job_directory
    report = deepcopy(contract["report"])
    report["export"] = {"job_id": identifier, **{key: value for key, value in result.items() if key != "path"},
        "download_path": f"/mangaflow/download/{identifier}"}
    root = job_directory(identifier)
    (root / "review.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# MangaFlow review report", "", f"Run: {report['run_id']}",
        f"Fully verified: {report['fully_verified']}", f"Export job: {identifier}", ""]
    for index, page in enumerate(report["pages"], 1):
        lines += [f"## {index}. {page['filename']}", "", f"Page: {page['image_id']}", f"Version: {page['version']}",
            f"Outcome: {page['review']['outcome']}; correction rounds: {page['correction_rounds']}", page["review"]["note"], ""]
        for key, check in page["review"]["checks"].items():
            lines.append(f"- {key}: {check['status']} — {check['reason']}")
        for item in page["preserved"]:
            lines.append(f"- Preserved artwork {item.get('region_id', item.get('crop', ''))}: {item['reason']}")
        for item in page["issues"]:
            lines.append(f"- Issue {item['category']} [{item['status']}]: {item['message']}")
        for task in page["failed_tasks"]:
            lines.append(f"- Task {task['id']} [{task['status']}]: {task['message']}")
        lines.append("")
    (root / "review.md").write_text("\n".join(lines), encoding="utf-8")
    return {kind: f"/mangaflow/reports/{identifier}/{kind}" for kind in ("json", "markdown")}

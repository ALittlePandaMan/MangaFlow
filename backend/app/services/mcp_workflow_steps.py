"""Advisory tool calls derived from published state; never dispatch work."""
from app.models import TextRegion
from app.services.file_export import current_render
from app.services.regions import STALE_CLEAN_PATH_KEY
from app.services.workflow_state import record, requested_stages
from sqlalchemy import select


def step(tool, arguments, reason, *, required_inputs=(), automatic=True):
    return {"tool": tool, "arguments": arguments, "required_inputs": list(required_inputs),
        "reason": reason, "automatic": automatic}


def review_steps(run, page, version, phase):
    return [step("page_review_plan", {"image_id": page.id, "phase": phase, "expected_version": version},
        "View the planned native images, paginate the plan, then re-plan with collected preview IDs."),
        step("workflow_review_page", {"run_id": run.id, "image_id": page.id, "phase": phase,
            "expected_versions": {page.id: version}}, "Submit your own observations; evidence_ready does not mean visual quality passed.",
            required_inputs=["operation_id", "outcome", "checks", "note", "evidence_ids",
                *(["region_ids", "preserved"] if phase == "preflight" else [])], automatic=False)]


def page_steps(db, run, receipt, page, version, tasks, *, configuration_stale, source_fingerprint, blockers):
    """Return a state label and safe, explicit calls with agent-owned inputs."""
    if page is None:
        return "page_deleted", [step("workflow_status", {"run_id": run.id},
            "A frozen page was deleted. Report the missing page; do not silently replace it.", automatic=False)]
    if configuration_stale:
        return "configuration_changed", [step("page_state", {"image_id": page.id},
            "Project settings differ from this run. Inspect changes; do not silently modify settings or recreate the run.", automatic=False)]
    active = [task for task in tasks if task.status in {"QUEUED", "RUNNING", "CANCELLING", "PAUSED"}]
    if active:
        return ("paused" if any(task.status == "PAUSED" for task in active) else "waiting"), [
            step("tasks_get_task" if task.status == "PAUSED" else "task_wait",
                {"task_id": task.id, **({} if task.status == "PAUSED" else {"kind": "processing", "seconds": 20})},
                "Paused work requires user direction; never resume automatically." if task.status == "PAUSED" else
                "Wait for the actual task, including work submitted outside this run; do not enqueue a duplicate.",
                automatic=task.status != "PAUSED") for task in active]
    linked = [task for task in tasks if task.payload.get("options", {}).get("workflow_run_id") == run.id]
    latest = linked[-1] if linked else None
    final = receipt.final_review
    if final and final.get("version") == version and not receipt.correction_open:
        if final["outcome"] == "issues" and final.get("disposition") != "deliver_with_issues" and receipt.correction_rounds < 2:
            return "correction_available", [step("workflow_begin_correction", {"run_id": run.id,
                "image_id": page.id, "expected_versions": {page.id: version}},
                "Open one correction round only if a specific local fix is justified; unresolved issues may also be delivered.",
                required_inputs=["operation_id"], automatic=False)]
        from app.services.mcp_batch_workflow import enabled
        if enabled(run) and final["outcome"] == "issues" and final.get("disposition") != "deliver_with_issues":
            actions = review_steps(run, page, version, "final")
            actions[-1]["arguments"].update(outcome="issues", disposition="deliver_with_issues")
            actions[-1]["required_inputs"].remove("outcome")
            actions[-1]["reason"] = "Correction budget exhausted. Inspect available evidence and explicitly record unresolved issues for delivery; export is blocked until this decision."
            return "delivery_decision", actions
        return "ready_for_delivery", []
    if latest and latest.status in {"FAILED", "CANCELLED"}:
        actions = [step("tasks_get_task", {"task_id": latest.id},
            "Inspect the failure/cancellation and any partial result before deciding; do not start the same operation again.", automatic=False)]
        if (latest.status == "FAILED" and latest.error_kind != "unknown_cloud_outcome"
                and latest.payload.get("options", {}).get("_workflow_retries", 0) < 2 and not final):
            actions.append(step("tasks_retry_task", {"task_id": latest.id, "workflow_run_id": run.id,
                "expected_versions": {page.id: version}}, "Only after establishing that this failed request can safely be retried; never approve unknown paid outcomes.",
                required_inputs=["operation_id"], automatic=False))
        actions.append(step("workflow_review_page", {"run_id": run.id, "image_id": page.id, "phase": "final",
            "outcome": "unverified", "expected_versions": {page.id: version}},
            "Alternatively record an explicit failure/unverified explanation for delivery; do not claim visual success.",
            required_inputs=["operation_id", "checks", "note"], automatic=False))
        return latest.status.lower(), actions
    from app.services.mcp_batch_workflow import enabled
    if enabled(run) and receipt.correction_open and any(
        review.get("version") == version and review.get("outcome") in {"issues", "unverified"}
        for review in (receipt.final_review or {}, receipt.preflight or {})):
        return "correcting", [step("page_state", {"image_id": page.id, "run_id": run.id, "include_details": True},
            "Read recorded issues, then use page_edit/mask_paint for the justified local fix. Do not re-submit the unchanged issues review. If no safe fix exists, explain it and deliver unresolved/unverified.", automatic=False)]
    rows = list(db.scalars(select(TextRegion).where(TextRegion.image_id == page.id).order_by(TextRegion.reading_order, TextRegion.id)))
    limited = set(run.options.get("region_ids") or [])
    scoped = [row for row in rows if not row.locked and (not limited or row.id in limited
        or (row.layout_data or {}).get("workflow_created_by") == run.id)]
    before = receipt.preflight
    preflight_current = bool(before and before.get("source_version") == source_fingerprint)
    selected = [row for row in scoped if preflight_current and row.id in before.get("region_ids", [])]
    from app.services.mcp_workflows import cleared_manual_mask
    restores = [row for row in scoped if preflight_current and row.id not in before.get("region_ids", [])
        and cleared_manual_mask(row, page)]
    cached_clean = page.clean_path or (page.metadata_json or {}).get(STALE_CLEAN_PATH_KEY)
    pending = [row for row in selected if not cached_clean or not row.pixel_mask_path
        or (row.layout_data or {}).get("mask_generation", {}).get("repair_pending")]
    repair_ready = not restores and (not selected or bool(page.clean_path) and not pending)
    stale_translation = any((row.layout_data or {}).get("translation_source_stale") for row in scoped
        if not preflight_current or row.id in before.get("region_ids", []))
    rendered = current_render({"page": record(page), "regions": {row.id: record(row) for row in rows}})
    if rendered and not stale_translation and repair_ready:
        return "final_review", review_steps(run, page, version, "final")
    if final and not receipt.correction_open:
        if receipt.correction_rounds < 2:
            if receipt.preflight.get("version") == version and receipt.preflight.get("outcome") == "issues":
                return "correction_available", [step("workflow_begin_correction", {"run_id": run.id,
                    "image_id": page.id, "expected_versions": {page.id: version}},
                    "Current source-image issues review permits recovery of the externally edited page within the existing correction budget.",
                    required_inputs=["operation_id"], automatic=False)]
            actions = review_steps(run, page, version, "preflight")
            actions[-1]["arguments"]["outcome"] = "issues"
            actions[-1]["required_inputs"].remove("outcome")
            actions[-1]["reason"] = "Inspect current original/source annotations and explain needed recovery. Submit preflight issues, then open a correction round; unavailable output is not a visual pass."
            return "stale_review", actions
        actions = review_steps(run, page, version, "final")
        if not page.rendered_path or rows and not page.clean_path:
            actions[-1] = step("workflow_review_page", {"run_id": run.id, "image_id": page.id,
                "phase": "final", "outcome": "unverified", "expected_versions": {page.id: version}},
                "An external edit invalidated the review and required output is unavailable. Inspect available previews, explain missing checks/repair needs, and record unverified for delivery. Existing correction/version guards still apply; do not bypass them with scripts.",
                required_inputs=["operation_id", "checks", "note"], automatic=False)
        return "stale_review", actions

    def process(start, end, ids=None, retranslate=None):
        required = set(requested_stages({"start_stage": start, "end_stage": end})) - {"mask"}
        if start == "translation" and all(row.source_text.strip() for row in rows if row.id in (ids or [])):
            required.discard("ocr")
        if end == "rendering":
            required |= {"font", "rendering"}
        missing = blockers(required=required)
        if missing:
            return "configuration_blocked", [step("workflow_status", {"run_id": run.id},
                "Required configuration is missing: " + ", ".join(item["kind"] for item in missing)
                + ". Report setup requirements; do not install models/libraries or silently change providers.", automatic=False)]
        options = {"region_ids": ids} if ids else {}
        if retranslate:
            options["retranslate_region_ids"] = retranslate
        return start, [step("images_process_image", {"image_id": page.id, "workflow_run_id": run.id,
            "expected_versions": {page.id: version}, "body": {"start_stage": start, "end_stage": end,
                "force": False, "options": options}},
            "Submit this stage range once, wait for its returned task ID, then refresh page_state/workflow_status.",
            required_inputs=["operation_id"])]

    from app.services.mcp_batch_workflow import preflight_current as text_checked
    from app.services.mcp_batch_workflow import text_request
    if enabled(run):
        # Text is safe to compute before visual erasure approval. Never combine
        # this preparation with a mask/repair stage, even on a fresh page.
        body = text_request(run, page, rows, tasks)
        if body:
            stage, actions = process(body["start_stage"], body["end_stage"], body["options"].get("region_ids"),
                body["options"].get("retranslate_region_ids"))
            if actions[0]["tool"] == "images_process_image":
                actions[0]["arguments"]["body"] = body
            return stage, actions
        if run.options["translation_mode"] == "agent":
            untranslated = [row for row in scoped if row.region_type != "sfx" and row.source_text.strip()
                and (not row.translated_text.strip() or (row.layout_data or {}).get("translation_source_stale"))]
            if untranslated:
                return "agent_translation", [step("regions_bulk_update_regions", {"body": [
                    {"id": row.id, "patch": {}} for row in untranslated], "workflow_run_id": run.id,
                    "expected_versions": {page.id: version}}, "Write translations for the ready text page; this is text preparation, not correction.",
                    required_inputs=["operation_id", "body[].patch.translated_text"], automatic=False)]
        if not text_checked(db, run, receipt):
            return "preflight_review", review_steps(run, page, version, "preflight")
        if before.get("outcome") != "passed":
            if receipt.correction_open:
                return "preflight_review", review_steps(run, page, version, "preflight")
            if receipt.correction_rounds < 2:
                return "correction_available", [step("workflow_begin_correction", {"run_id": run.id,
                    "image_id": page.id, "expected_versions": {page.id: version}},
                    "After the text batch ends, open a correction round for recorded text/source issues.",
                    required_inputs=["operation_id"], automatic=False)]
            return "preflight_unresolved", [step("workflow_review_page", {"run_id": run.id, "image_id": page.id,
                "phase": "final", "outcome": "unverified", "expected_versions": {page.id: version}},
                "Text issues remain after two rounds. Report unverified; do not erase unapproved artwork.",
                required_inputs=["operation_id", "checks", "note"], automatic=False)]

    # Published page state or a successful task distinguishes a genuine
    # zero-text page from a page whose detector has never run.
    detection_done = page.status not in {"UPLOADED", "DETECTING", "FAILED"} or any(
        task.status == "COMPLETED" and "detection" in requested_stages(task.payload) for task in tasks)
    if not rows and not detection_done:
        return process("detection", "ocr")
    if not preflight_current:
        if page.clean_path and scoped and all(row.translated_text.strip() and row.pixel_mask_path
                and not (row.layout_data or {}).get("translation_source_stale")
                and not (row.layout_data or {}).get("mask_generation", {}).get("repair_pending") for row in scoped):
            # Pure layout edits consume the existing background and never erase
            # or translate. They do not require a new source allowlist.
            return process("rendering", "rendering")
        missing_ocr = [row.id for row in scoped if not row.source_text.strip()
            and "ocr" not in (row.layout_data or {}) and "empty_source_text" not in (row.review_reasons or [])]
        if missing_ocr:
            return process("ocr", "ocr", missing_ocr)
        # An observed empty OCR result also needs inspection, not an endless
        # retry. Nonempty/manual text is never overwritten by this suggestion.
        return "preflight_review", review_steps(run, page, version, "preflight")
    allowed = set(before.get("region_ids", []))
    selected = [row for row in scoped if row.id in allowed]
    if restores and not pending:
        # Restoring cleared masks precedes rendering even when all regions
        # are excluded. Other masks/repairs are preserved by incremental repair.
        # Finish pending selected repairs first: restoration must not create
        # a clean cache that makes never-repaired masks appear completed.
        return process("inpainting", "rendering", [row.id for row in restores])
    if not selected:
        return process("rendering", "rendering")
    untranslated = [row for row in selected if row.source_text.strip()
        and (not row.translated_text.strip() or (row.layout_data or {}).get("translation_source_stale"))]
    if untranslated:
        if run.options["translation_mode"] == "configured":
            end = "translation" if cached_clean else "rendering"
            # Nonempty translations are preserved by force=False. Include all
            # approved regions in erasure, even those translated manually.
            # With a cached background, translate separately so a later local
            # repair does not also re-inpaint already completed translations.
            return process("translation", end, [row.id for row in (untranslated if cached_clean else selected)],
                [row.id for row in untranslated if (row.layout_data or {}).get("translation_source_stale")])
        return "agent_translation", [step("regions_bulk_update_regions", {"body": [
            {"id": row.id, "patch": {}} for row in untranslated], "workflow_run_id": run.id,
            "expected_versions": {page.id: version}}, "Write translated_text for each listed region using the verified OCR/context; then refresh the page state.",
            required_inputs=["operation_id", "body[].patch.translated_text"], automatic=False)]
    if any(not row.pixel_mask_path for row in selected):
        return process("mask", "rendering", [row.id for row in pending])
    if not page.clean_path or pending:
        return process("inpainting", "rendering", [row.id for row in (pending or selected)])
    return process("rendering", "rendering")

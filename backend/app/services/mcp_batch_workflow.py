"""Small policy helpers for the two-wave MCP recipe; the queue owns execution."""
from app.models import ProcessingTask, TextRegion
from app.services.workflow_state import fingerprint, requested_stages
from fastapi import HTTPException
from sqlalchemy import select


def enabled(run):
    return run.options.get("execution_mode") == "batch_text_review"


def text_version(db, image_id):
    from app.services.mcp_workflows import source_version
    return fingerprint({"source": source_version(db, image_id), "text": [
        (row.id, row.translated_text, bool((row.layout_data or {}).get("translation_source_stale")))
        for row in db.scalars(select(TextRegion).where(TextRegion.image_id == image_id).order_by(TextRegion.id))]})


def preflight_current(db, run, receipt):
    from app.services.mcp_workflows import source_version
    before = receipt.preflight
    return bool(before and before.get("source_version") == source_version(db, receipt.image_id)
        and (not enabled(run) or before.get("text_version") == text_version(db, receipt.image_id)))


def text_request(run, page, rows, tasks):
    """Plan only missing text, never redetect existing regions or erase artwork."""
    limited = set(run.options.get("region_ids") or [])
    eligible = [row for row in rows if not row.locked and row.region_type != "sfx"
        and (not limited or row.id in limited or (row.layout_data or {}).get("workflow_created_by") == run.id)]
    detected = bool(page.rendered_path) or page.status not in {"UPLOADED", "DETECTING", "FAILED"} or any(
        task.status == "COMPLETED" and "detection" in requested_stages(task.payload) for task in tasks)
    end = "translation" if run.options["translation_mode"] == "configured" else "ocr"
    if not rows and not detected and not limited:
        return {"start_stage": "detection", "end_stage": end, "force": False,
            "options": {"_workflow_text_only": True}}
    stale_ocr = [row.id for row in eligible if (row.layout_data or {}).get("ocr_geometry_stale")]
    missing_ocr = [row for row in eligible if row.id in stale_ocr or not row.source_text.strip()
        and "ocr" not in (row.layout_data or {}) and "empty_source_text" not in (row.review_reasons or [])]
    missing_translation = [row for row in eligible if row.source_text.strip()
        and (not row.translated_text.strip() or (row.layout_data or {}).get("translation_source_stale"))]
    if not missing_ocr and (not missing_translation or end == "ocr"):
        return None
    selected = list({row.id: row for row in [*missing_ocr, *missing_translation]}.values())
    return {"start_stage": "ocr" if missing_ocr else "translation", "end_stage": end, "force": False,
        "options": {"region_ids": [row.id for row in selected], "_workflow_text_only": True,
            "reocr_region_ids": stale_ocr,
            "retranslate_region_ids": [row.id for row in selected if (row.layout_data or {}).get("translation_source_stale")]}}


def active_tasks(db, run):
    from app.services.mcp_workflows import ACTIVE
    return list(db.scalars(select(ProcessingTask).where(ProcessingTask.image_id.in_(run.page_ids),
        ProcessingTask.status.in_(ACTIVE))))


def assert_correction_barrier(db, run, image_id=None):
    """Called under the project publication lock, including direct tool writes."""
    if not enabled(run):
        return
    from app.services.mcp_coordination import correction_wave, parallel
    if parallel(run) and image_id:
        from app.services.mcp_workflows import _quiet, page_row
        receipt = page_row(db, run.id, image_id)
        _quiet(db, image_id)
        if receipt.correction_open or run.options.get(correction_wave(receipt)+"_corrections_ready"):
            return
    if active_tasks(db, run):
        raise HTTPException(409, "Batch still has unfinished/paused tasks; record reviews now and correct after it ends")
    if parallel(run) and image_id and correction_wave(receipt) == "output" and run.options.get("output_started"):
        from app.services.mcp_workflow_runtime import snapshot
        from app.services.mcp_workflows import page_row
        for identifier in run.page_ids:
            other = page_row(db, run.id, identifier)
            _, actions = snapshot(db, run, other, False)
            if any(action["tool"] == "images_process_image" and not action["arguments"]["body"]["options"].get("_workflow_text_only") for action in actions):
                raise HTTPException(409, "Queue remaining approved output pages before final corrections")
    if run.options.get("batch_phase", "text") == "text":
        from app.models import ImagePage
        for page in db.scalars(select(ImagePage).where(ImagePage.id.in_(run.page_ids))):
            tasks = list(db.scalars(select(ProcessingTask).where(ProcessingTask.image_id == page.id)))
            if any(task.status in {"FAILED", "CANCELLED"} and task.payload.get("options", {}).get("workflow_run_id") == run.id for task in tasks):
                continue  # Explicit task recovery remains a separate decision.
            rows = list(db.scalars(select(TextRegion).where(TextRegion.image_id == page.id)))
            if text_request(run, page, rows, tasks):
                raise HTTPException(409, "Queue the remaining text batch before starting corrections")


def correction_phase(run, *, wave=None):
    from app.services.mcp_coordination import parallel
    extra = {wave+"_corrections_ready": True} if parallel(run) and wave else {}
    run.options = {**run.options, "batch_phase": "output",
        "output_started": bool(parallel(run) and run.options.get("output_started")), **extra}

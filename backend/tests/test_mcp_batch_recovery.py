"""Local OCR refresh, corrected context and terminal delivery recovery over MCP."""
import pytest
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import FileJob, MCPOperation, MCPWorkflowPage, MCPWorkflowRun, ProcessingTask, TextRegion
from app.services.mcp_workflows import delivery_version
from sqlalchemy import func, select
from test_checkpoint_pipeline import checkpoint_mode as checkpoint_mode
from test_mcp import mutation, page, rpc
from test_mcp_batch_workflow import advance, book, status
from test_mcp_batch_workflow import configured as configured
from test_mcp_workflows import begin, evidence, ok, region, render_empty, review, state
from test_translation_auto_ocr import providers as providers


def open_text_correction(client, run, image):
    ok(review(client, run, image, phase="preflight", disposition="defer"))
    return ok(rpc(client, "workflow_begin_correction", mutation(run_id=run, image_id=image["id"],
        expected_versions=state(client, image)["expected_versions"])))


def edit(client, run, image, patch):
    return ok(rpc(client, "page_edit", mutation(image_id=image["id"], workflow_run_id=run,
        update=[{"region_id": "R001", "patch": patch}], expected_versions=state(client, image)["expected_versions"])))


def finish(client, task):
    result = ok(rpc(client, "task_wait", {"task_id": task["id"], "kind": "processing", "seconds": 20}))
    assert result["status"] == "COMPLETED", result


def test_exhausted_defer_requires_explicit_delivery_before_export(client):
    project, image = page(client)
    render_empty(image)
    run = begin(client, project, legacy=False)
    ok(review(client, run, image, disposition="defer"))
    for _ in range(2):
        ok(rpc(client, "workflow_begin_correction", mutation(run_id=run, image_id=image["id"],
            expected_versions=state(client, image)["expected_versions"])))
        ok(review(client, run, image, disposition="defer"))
    current = status(client, run)
    assert current["pages"][0]["execution_stage"] == "delivery_decision"
    assert all(step["tool"] != "workflow_export" for step in current["next_steps"])
    decision = current["next_steps"][-1]
    assert decision["tool"] == "workflow_review_page"
    assert decision["arguments"]["disposition"] == "deliver_with_issues"
    assert not decision["automatic"]
    assert rpc(client, "workflow_export", mutation(run_id=run))["isError"]
    ok(review(client, run, image, disposition=decision["arguments"]["disposition"]))
    assert status(client, run)["next_steps"][0]["tool"] == "workflow_export"
    job = ok(rpc(client, "workflow_export", mutation(run_id=run)))["job"]
    assert ok(rpc(client, "task_wait", {"task_id": job["id"], "kind": "file", "seconds": 20}))["status"] == "COMPLETED"


@pytest.mark.parametrize("checkpoint_enabled", [True, False])
def test_source_frame_refreshes_only_stale_ocr_and_translation(client, providers, monkeypatch, checkpoint_enabled):
    monkeypatch.setattr(get_settings(), "checkpoint_pipeline_enabled", checkpoint_enabled)
    project, image = page(client)
    first = region(client, image)
    manual = region(client, image, bbox=[5, 5, 20, 25], source_text="人工原文", translated_text="Keep manual")
    with SessionLocal() as db:
        target = db.get(TextRegion, first["id"])
        target.layout_data = {**target.layout_data, "ocr": {"provider": "previous-ocr"}}
        db.commit()
    run = begin(client, project, legacy=False)
    open_text_correction(client, run, image)
    edit(client, run, image, {"bbox": [130, 40, 90, 100]})
    before = state(client, image)
    assert before["regions"][0]["state"]["ocr_geometry_stale"]
    rejected = rpc(client, "workflow_review_page", mutation(run_id=run, image_id=image["id"], phase="preflight",
        outcome="passed", checks={name: {"status": "pass", "reason": "Inspected"} for name in
            ("geometry", "false_detection", "missing_text", "ocr", "translation")},
        note="Cannot approve stale OCR", selection={"mode": "all_eligible"},
        evidence_ids=evidence(client, image, "preflight"),
        expected_versions=before["expected_versions"]))
    assert rejected["isError"] and "stale" in str(rejected)
    providers[0].responses[130] = "新区域的原文"
    task = ok(advance(client, run, [image]))["tasks"][0]
    with SessionLocal() as db:
        payload = db.get(ProcessingTask, task["id"]).payload
        assert payload["start_stage"] == "ocr" and payload["end_stage"] == "translation"
        assert payload["options"]["reocr_region_ids"] == [first["id"]]
    finish(client, task)
    assert providers[0].calls == [([130, 40, 90, 100], first["orientation"], 4)]
    current = state(client, image)
    rows = {row["id"]: row for row in current["regions"]}
    assert rows[first["id"]]["source_text"] == "新区域的原文"
    assert rows[first["id"]]["translated_text"] == "译文:新区域的原文"
    assert not rows[first["id"]]["state"]["ocr_geometry_stale"]
    assert not rows[first["id"]]["state"]["translation_source_stale"]
    assert rows[manual["id"]]["source_text"] == "人工原文"
    assert rows[manual["id"]]["translated_text"] == "Keep manual"
    assert not ok(advance(client, run, [image]))["tasks"]
    with SessionLocal() as db:
        assert not any((db.get(TextRegion, first["id"]).pixel_mask_path,
            db.get(TextRegion, first["id"]).page.clean_path, db.get(TextRegion, first["id"]).page.rendered_path))


def test_explicit_source_text_confirms_new_frame_without_overwriting_manual_text(client):
    project, image = page(client)
    row = region(client, image)
    run = begin(client, project, legacy=False)
    open_text_correction(client, run, image)
    edit(client, run, image, {"bbox": [130, 40, 90, 100], "source_text": row["source_text"]})
    assert not state(client, image)["regions"][0]["state"]["ocr_geometry_stale"]
    assert not ok(advance(client, run, [image]))["tasks"]
    ok(review(client, run, image, phase="preflight", outcome="passed", selection={"mode": "all_eligible"}))


def test_refreshed_identical_ocr_preserves_manual_translation(client, providers):
    project, image = page(client)
    row = region(client, image, translated_text="Manual translation")
    run = begin(client, project, legacy=False)
    open_text_correction(client, run, image)
    edit(client, run, image, {"bbox": [130, 40, 90, 100]})
    providers[0].responses[130] = row["source_text"]
    finish(client, ok(advance(client, run, [image]))["tasks"][0])
    assert len(providers[0].calls) == 1
    assert not providers[1].calls
    assert state(client, image)["regions"][0]["translated_text"] == "Manual translation"


def test_clearing_existing_ocr_retries_once_and_empty_result_does_not_loop(client, providers):
    project, image = page(client)
    row = region(client, image)
    with SessionLocal() as db:
        target = db.get(TextRegion, row["id"])
        target.layout_data = {**target.layout_data, "ocr": {"old": True}}
        target.review_reasons = ["empty_source_text"]
        db.commit()
    run = begin(client, project, legacy=False)
    open_text_correction(client, run, image)
    edit(client, run, image, {"source_text": "", "translated_text": ""})
    providers[0].responses[30] = ""
    task = ok(advance(client, run, [image]))["tasks"][0]
    finish(client, task)
    assert len(providers[0].calls) == 1
    assert not providers[1].calls
    assert state(client, image)["regions"][0]["source_text"] == ""
    assert not ok(advance(client, run, [image]))["tasks"]
    assert status(client, run)["pages"][0]["execution_stage"] == "preflight_review"


def test_previous_context_uses_manually_corrected_published_translation(client, providers):
    project, images = book(client, 2)
    for image in images:
        region(client, image, source_text="Initial source", translated_text="")
    client.patch(f"/api/projects/{project['id']}", json={"settings": {"translation_context_mode": "previous_page"}})
    run = begin(client, project, legacy=False)
    for task in ok(advance(client, run, images))["tasks"]:
        finish(client, task)
    for image in images:
        open_text_correction(client, run, image)
    corrected = "MANUALLY CORRECTED PREVIOUS PAGE"
    edit(client, run, images[0], {"translated_text": corrected})
    edit(client, run, images[1], {"source_text": "Changed source"})
    task = ok(advance(client, run, [images[1]]))["tasks"][0]
    with SessionLocal() as db:
        assert db.get(ProcessingTask, task["id"]).dependency_task_id is None
    finish(client, task)
    assert corrected in str(providers[1].calls[-1][1]["previous_page"])
    assert state(client, images[0])["regions"][0]["translated_text"] == corrected


@pytest.mark.parametrize(("terminal_status", "format_name"), [("FAILED", "translated"), ("CANCELLED", "translated"), ("FAILED", "psd")])
def test_terminal_export_has_explicit_resubmission_and_preserves_format(client, terminal_status, format_name):
    project, image = page(client)
    render_empty(image)
    run_id = begin(client, project, legacy=False)
    ok(review(client, run_id, image, outcome="unverified", evidence_ids=[]))
    with SessionLocal() as db:
        run = db.get(MCPWorkflowRun, run_id)
        receipts = list(db.scalars(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run_id)))
        job = FileJob(project_id=project["id"], kind=format_name, status=terminal_status, message="Terminal export",
            result={"workflow_delivery_version": delivery_version(db, run, receipts), "error": "Temporary disk failure"})
        db.add(job)
        db.flush()
        identifier = job.id
        db.add(MCPOperation(id=mutation()["operation_id"], fingerprint="f"*64, status="COMPLETED", workflow_run_id=run_id,
            result={"structuredContent": {"job": {"id": identifier}}}))
        db.commit()
    current = status(client, run_id)
    suggestion = current["next_steps"][0]
    assert suggestion["tool"] == "workflow_export" and not suggestion["automatic"]
    assert suggestion["arguments"]["kind"] == (format_name if format_name in {"translated", "cbz"} else "cbz")
    assert not current["exports"][0]["current"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(FileJob).where(FileJob.project_id == project["id"])) == 1
    exported = ok(rpc(client, suggestion["tool"], mutation(**suggestion["arguments"])))
    job = ok(rpc(client, "task_wait", {"task_id": exported["job"]["id"], "kind": "file", "seconds": 20}))
    assert job["status"] == "COMPLETED", job
    final = status(client, run_id)
    assert final["next_steps"][0]["tool"] == "task_wait"
    assert final["next_steps"][0]["arguments"]["task_id"] == exported["job"]["id"]
    assert final["exports"][0]["current"]

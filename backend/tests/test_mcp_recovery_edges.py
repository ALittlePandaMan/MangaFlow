"""Regression coverage for mixed manual work, reconnects and delivery fences."""
import io
from uuid import uuid4
from zipfile import ZipFile

import pytest
from app.core.database import SessionLocal
from app.models import FileJob, MCPOperation, MCPWorkflowPage, ProcessingTask, StageCheckpoint, TaskWorkflow, TextRegion
from app.services import file_export, file_jobs, mcp_workflows
from app.services.file_jobs import file_worker
from app.tasks import task_manager
from PIL import Image
from sqlalchemy import func, select
from test_mcp import mutation, page, rpc
from test_mcp_workflow_recovery import process
from test_mcp_workflow_recovery import providers as providers
from test_mcp_workflows import begin, ok, region, render_empty, review, state


@pytest.fixture(autouse=True)
def configured(client, monkeypatch):
    client.headers["Host"] = "localhost"
    monkeypatch.setattr(mcp_workflows, "blockers", lambda db, run, **kwargs: [])


def unknown(run):
    identifier = "lost-" + str(uuid4())
    with SessionLocal() as db:
        db.add(MCPOperation(id=identifier, workflow_run_id=run, fingerprint="a" * 64, status="UNKNOWN", result={}))
        db.commit()
    return identifier


def test_mixed_manual_translations_receive_erasure_without_retranslation(client, providers):
    project, image = page(client)
    untranslated = region(client, image, translated_text="")
    manual = region(client, image, bbox=[185, 20, 90, 100], translated_text="Keep manual text")
    run = begin(client, project)
    ok(review(client, run, image, phase="preflight", outcome="unverified", evidence_ids=[],
        region_ids=[untranslated["id"], manual["id"]]))
    process(client, image, run)
    with SessionLocal() as db:
        assert all(db.get(TextRegion, row["id"]).pixel_mask_path for row in (untranslated, manual))
        assert db.get(TextRegion, manual["id"]).translated_text == "Keep manual text"
        assert not db.get(TextRegion, manual["id"]).layout_data.get("mask_generation", {}).get("repair_pending")
    assert providers == [[(untranslated["region_key"], untranslated["source_text"])]]
    assert ok(rpc(client, "workflow_status", {"run_id": run}))["pages"][0]["execution_stage"] == "final_review"
    # A current rendered composite alone cannot prove all approved masks exist.
    with SessionLocal() as db:
        db.get(TextRegion, manual["id"]).pixel_mask_path = None
        db.commit()
    assert ok(rpc(client, "workflow_status", {"run_id": run}))["pages"][0]["execution_stage"] == "mask"


def test_unknown_can_accept_inspected_current_state_and_resume_without_replay(client, monkeypatch):
    project, image = page(client)
    run = begin(client, project)
    operation = unknown(run)
    inspected = ok(rpc(client, "operation_status", {"operation_id": operation}))
    assert inspected["status"] == "UNKNOWN" and inspected["recovery"]["run_id"] == run
    assert inspected["recovery"]["arguments"] == {"operation_id": operation, "accept_current_state": True}
    assert inspected["recovery"]["inspect"]["tool"] == "workflow_status"
    assert ok(rpc(client, "workflow_status", {"run_id": run}))["next_steps"][0]["tool"] == "operation_status"
    args = {"operation_id": operation, "accept_current_state": True,
        "expected_versions": state(client, image)["expected_versions"], "note": "Inspected published page; no accepted task exists"}
    accepted = ok(rpc(client, "operation_status", args))
    assert accepted["status"] == "ACKNOWLEDGED" and accepted["result"]["replayed"] is False
    assert ok(rpc(client, "operation_status", args)) == accepted
    assert ok(rpc(client, "workflow_status", {"run_id": run}))["next_steps"][0]["tool"] == "workflow_advance"
    monkeypatch.setattr(task_manager, "dispatch", lambda identifier: None)
    queued = ok(rpc(client, "workflow_advance", mutation(run_id=run, image_id=image["id"],
        expected_versions=state(client, image)["expected_versions"])))
    assert queued["task"]["status"] == "QUEUED"
    with SessionLocal() as db:
        assert db.scalar(select(func.count(ProcessingTask.id)).where(ProcessingTask.image_id == image["id"])) == 1


@pytest.mark.parametrize("unsafe", ["version", "active", "cloud_checkpoint", "cloud_task"])
def test_unknown_acknowledgement_refuses_unsafe_recovery(client, unsafe):
    project, image = page(client)
    run = begin(client, project)
    operation = unknown(run)
    versions = state(client, image)["expected_versions"]
    if unsafe == "version":
        versions = {image["id"]: "obsolete"}
    else:
        with SessionLocal() as db:
            task = ProcessingTask(project_id=project["id"], image_id=image["id"], task_type="translation",
                status="RUNNING" if unsafe == "active" else "FAILED",
                error_kind="unknown_cloud_outcome" if unsafe == "cloud_task" else None)
            db.add(task)
            db.flush()
            if unsafe == "cloud_checkpoint":
                workflow = TaskWorkflow(task_id=task.id, generation=1, input_fingerprint="a" * 64,
                    request_fingerprint="b" * 64, input_state={}, policy={})
                db.add(workflow)
                db.flush()
                db.add(StageCheckpoint(workflow_id=workflow.id, stage="translation", attempt=1, generation=1,
                    input_fingerprint="a" * 64, status="UNKNOWN", cloud_request=True))
            db.commit()
    response = rpc(client, "operation_status", {"operation_id": operation, "accept_current_state": True,
        "expected_versions": versions, "note": "Attempt inspected-state recovery"})
    assert response["isError"] and response["structuredContent"]["error"]["status"] == 409
    assert ok(rpc(client, "operation_status", {"operation_id": operation}))["status"] == "UNKNOWN"


def test_unknown_exact_accepted_task_reconciles_and_identical_retry_never_requeues(client, monkeypatch):
    from app.services.mcp_server import recover_operations
    project, image = page(client)
    run = begin(client, project)
    monkeypatch.setattr(task_manager, "dispatch", lambda identifier: None)
    args = mutation(run_id=run, image_id=image["id"], expected_versions=state(client, image)["expected_versions"])
    first = ok(rpc(client, "workflow_advance", args))
    with SessionLocal() as db:
        row = db.get(MCPOperation, args["operation_id"])
        row.status, row.result = "RUNNING", {"_request": {"tool": "workflow_advance"}}
        db.commit()
    recover_operations()
    restored = ok(rpc(client, "operation_status", {"operation_id": args["operation_id"]}))
    assert restored["status"] == "COMPLETED" and restored["result"]["task"]["id"] == first["task"]["id"]
    assert ok(rpc(client, "workflow_advance", args))["task"]["id"] == first["task"]["id"]
    assert ok(rpc(client, "workflow_status", {"run_id": run}))["next_steps"][0]["tool"] == "task_wait"
    with SessionLocal() as db:
        assert db.scalar(select(func.count(ProcessingTask.id)).where(ProcessingTask.image_id == image["id"])) == 1


def change_receipt(run):
    with SessionLocal() as db:
        row = db.scalar(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run))
        row.final_review = {**row.final_review, "outcome": "issues", "note": "Damage discovered before snapshot",
            "disposition": "deliver_with_issues"}
        db.commit()


@pytest.mark.parametrize("when", ["before_snapshot", "during_copy", "after_snapshot"])
def test_export_binds_review_records_until_snapshot_is_sealed(client, monkeypatch, when):
    project, image = page(client)
    render_empty(image)
    run = begin(client, project)
    ok(review(client, run, image, outcome="passed"))
    if when == "before_snapshot":
        original = file_worker.export_file

        def changed(identifier, project_id, kind):
            change_receipt(run)
            return original(identifier, project_id, kind)

        monkeypatch.setattr(file_worker, "export_file", changed)
    elif when == "during_copy":
        original = file_jobs._archive_paths

        def changed(*args, **kwargs):
            change_receipt(run)
            return original(*args, **kwargs)

        monkeypatch.setattr(file_jobs, "_archive_paths", changed)
    else:
        original = file_export.export_snapshot

        def changed(*args, **kwargs):
            change_receipt(run)
            return original(*args, **kwargs)

        monkeypatch.setattr(file_export, "export_snapshot", changed)
    exported = ok(rpc(client, "workflow_export", mutation(run_id=run)))
    job = ok(rpc(client, "task_wait", {"task_id": exported["job"]["id"], "kind": "file", "seconds": 20}))
    assert ok(rpc(client, "workflow_status", {"run_id": run}))["counts"]["issues"] == 1
    if when == "after_snapshot":
        assert job["status"] == "COMPLETED", job
        assert client.get(exported["report_downloads"]["json"]).json()["fully_verified"] is True
        assert not ok(rpc(client, "workflow_status", {"run_id": run}))["exports"][0]["current"]
    else:
        assert job["status"] == "FAILED", job
        assert "review records changed" in job["message"]
        assert client.get(exported["report_downloads"]["json"]).status_code == 409


@pytest.mark.parametrize("kind", ["cbz", "pdf"])
def test_container_import_identity_survives_job_cleanup(client, kind):
    data = io.BytesIO()
    picture = io.BytesIO()
    Image.new("RGB", (20, 20), "white").save(picture, "PNG")
    if kind == "cbz":
        with ZipFile(data, "w") as archive:
            archive.writestr("1.png", picture.getvalue())
    else:
        Image.new("RGB", (20, 20), "white").save(data, "PDF")
    asset = client.post("/mangaflow/files", params={"filename": "漫画." + kind}, content=data.getvalue()).json()["asset_id"]
    args = mutation(asset_id=asset, source_fingerprint="b" * 64)
    imported = ok(rpc(client, "project_import", args))
    assert imported["source_fingerprint"] == "b" * 64
    job = ok(rpc(client, "task_wait", {"task_id": imported["job"]["id"], "kind": "file", "seconds": 20}))
    assert job["status"] == "COMPLETED", job
    from app.services.mcp_directory_import import import_project
    assert import_project(args)["source_fingerprint"] == "b" * 64
    # A committed file job also restores a receipt lost during server shutdown.
    with SessionLocal() as db:
        row = db.get(MCPOperation, args["operation_id"])
        row.status, row.result = "UNKNOWN", {"_request": {"tool": "project_import"}}
        db.commit()
    restored = ok(rpc(client, "operation_status", {"operation_id": args["operation_id"]}))
    assert restored["status"] == "COMPLETED" and restored["result"]["source_fingerprint"] == "b" * 64
    with SessionLocal() as db:
        db.delete(db.get(FileJob, job["id"]))
        db.commit()
    recovered = ok(rpc(client, "project_import_status", {"operation_id": args["operation_id"], "source_fingerprint": "b" * 64}))
    assert not recovered["found"]
    assert recovered["operation"]["result"]["structuredContent"]["project_id"] == imported["project_id"]
    assert rpc(client, "project_import_status", {"operation_id": args["operation_id"], "source_fingerprint": "c" * 64})["isError"]

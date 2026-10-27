"""Comic-only MCP contract, atomic corrections and compact recovery."""
import io
import json
from uuid import uuid4
from zipfile import ZipFile

import pytest
from app.core.database import SessionLocal
from app.models import (
    AgentReviewIssue,
    EditorHistoryEntry,
    ImagePage,
    MCPOperation,
    MCPWorkflowPage,
    MCPWorkflowRun,
    ProcessingTask,
    TextRegion,
)
from app.services import mcp_workflows
from app.services.mcp_catalog import CUSTOM
from app.services.mcp_workflow_definitions import PREFLIGHT_CHECKS
from app.tasks import task_manager
from jsonschema import validate
from PIL import Image
from sqlalchemy import func, select
from test_mcp import mutation, page, rpc
from test_mcp_workflows import begin, evidence, ok, region, render_empty, review, state


@pytest.fixture(autouse=True)
def configured(client, monkeypatch):
    client.headers["Host"] = "localhost"
    monkeypatch.setattr(mcp_workflows, "blockers", lambda db, run, **kwargs: [])


def call_protocol(client, method, params=None):
    return client.post("/mangaflow", headers={"Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params else {})}).json()


def test_fixed_tools_no_management_or_generic_escape_and_small_schema(client):
    tools = call_protocol(client, "tools/list")["result"]["tools"]
    expected = {"mcp_status", "workflow_get", "project_list", "project_import", "project_import_status", "workflow_start",
        "workflow_status", "workflow_advance", "page_state", "page_review_plan", "page_preview", "workflow_review_page",
        "workflow_begin_correction", "page_edit", "mask_paint", "task_wait", "task_cancel", "task_retry", "operation_status", "workflow_export"}
    assert {item["name"] for item in tools} == expected
    assert len(tools) == 20
    assert len(json.dumps(tools, ensure_ascii=False).encode()) <= 35*1024
    for removed in ("models_bootstrap", "mcp_configure", "projects_delete_project", "regions_patch_region", "history_undo", "workflow_list", "project_import_images"):
        response = call_protocol(client, "tools/call", {"name": removed, "arguments": {}})
        assert response["error"]["code"] == -32602
    assert not ok(rpc(client, "mcp_status"))["connection_capabilities"]["local_import"]


def test_single_recipe_prompts_and_real_tool_examples(client):
    schemas = {tool.name: tool.inputSchema for tool in CUSTOM}
    for prompt in ("translate_and_review", "review_and_fix", "repair_regions"):
        guide = ok(rpc(client, "workflow_get", {"workflow_id": prompt}))
        assert guide["version"] == "2.2.0"
        assert guide["execution_policy"] == {"client_scripts": False, "install_dependencies": False}
        assert len(json.dumps(guide)) < 8500
        for example in guide["examples"]:
            validate(example["arguments"], schemas[example["tool"]])
        result = call_protocol(client, "prompts/get", {"name": prompt})["result"]
        assert result["messages"][0]["content"]["text"] == guide["instructions"]


def edit(client, image, **changes):
    return rpc(client, "page_edit", mutation(image_id=image["id"], expected_versions=state(client, image)["expected_versions"], **changes))


def test_compact_page_and_region_keys_keep_complete_text_and_geometry(client):
    _, image = page(client)
    text = region(client, image, source_text="combining é "*100, translated_text="译文"*100)
    before = state(client, image)
    item = before["regions"][0]
    assert item["source_text"] == text["source_text"] and item["translated_text"] == text["translated_text"]
    assert "original_url" not in before["page"] and "mask_url" not in item and "layout_data" not in item
    assert "polygon" not in item and "translated_bbox" not in item
    ok(edit(client, image, update=[{"region_id": item["region_key"], "patch": {"translated_text": "Corrected", "rotation": 0}}]))
    after = state(client, image)
    assert after["regions"][0]["translated_text"] == "Corrected" and after["version"] != before["version"]
    details = ok(rpc(client, "page_state", {"image_id": image["id"], "include_details": True}))
    assert "font_size" in details["regions"][0]["style"]


def test_atomic_replacement_one_history_entry_and_undo(client):
    project, image = page(client)
    old = region(client, image)
    with SessionLocal() as db:
        count = db.scalar(select(func.count(EditorHistoryEntry.id)).where(EditorHistoryEntry.image_id == image["id"]))
    changed = ok(edit(client, image, delete=[old["region_key"]], add=[{"bbox": [20, 20, 40, 80], "source_text": "一"},
        {"bbox": [70, 20, 40, 80], "source_text": "二"}]))
    assert len(changed["created"]) == 2 and changed["deleted_count"] == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count(EditorHistoryEntry.id)).where(EditorHistoryEntry.image_id == image["id"])) == count+1
    cursor = client.get(f"/api/projects/{project['id']}/history").json()
    result = client.post(f"/api/projects/{project['id']}/history/undo", json={"expected_entry_id": cursor["undo_id"]})
    assert result.status_code == 200, result.text
    assert [item["id"] for item in state(client, image)["regions"]] == [old["id"]]


def test_text_only_edit_does_not_renumber_other_regions(client):
    _, image = page(client)
    first = region(client, image)
    second = region(client, image, bbox=[180, 20, 50, 80])
    with SessionLocal() as db:
        db.get(TextRegion, second["id"]).region_key = "R005"
        db.commit()
    ok(edit(client, image, update=[{"region_id": first["id"], "patch": {"source_text": "Corrected OCR"}}]))
    assert next(row for row in state(client, image)["regions"] if row["id"] == second["id"])["region_key"] == "R005"


def test_structural_fallback_rebuild_uses_its_published_version_and_history(client, monkeypatch):
    from app.services import region_deletion
    project, image = page(client)
    text = region(client, image)
    render_empty(image)
    run = begin(client, project)
    monkeypatch.setattr(region_deletion, "restore_deleted_pixels", lambda *args: False)
    monkeypatch.setattr(task_manager, "dispatch", lambda identifier: None)
    response = ok(edit(client, image, workflow_run_id=run, delete=[text["id"]], add=[{"bbox": [30, 40, 40, 50]}]))
    assert response["rebuild_task"]["id"]
    with SessionLocal() as db:
        task = db.get(ProcessingTask, response["rebuild_task"]["id"])
        assert task.payload["_mcp_admission_version"] == response["version"]
        assert task.payload["options"]["_editor_history_entry_id"]
        assert task.payload["options"]["workflow_run_id"] == run
    ok(rpc(client, "task_cancel", mutation(task_id=response["rebuild_task"]["id"], kind="processing")))


@pytest.mark.parametrize("bad", [
    {"locked": False}, {"layout_data": {"manual": False}}, {"bbox": [0, 0, -1, 10]},
    {"polygon": [[20, 20], [80, 80], [20, 80], [80, 20]]}, {"translated_bbox": [290, 20, 50, 20]},
])
def test_invalid_batch_does_not_publish_any_text_or_delete(client, bad):
    _, image = page(client)
    first = region(client, image)
    other = region(client, image, bbox=[160, 20, 40, 80])
    before = state(client, image)
    response = edit(client, image, delete=[other["id"]], update=[{"region_id": first["id"], "patch": {"translated_text": "must rollback", **bad}}])
    assert response["isError"]
    assert state(client, image)["version"] == before["version"]


def test_stale_key_or_foreign_reference_cannot_edit_new_page_state(client):
    _, image = page(client)
    first = region(client, image)
    before = state(client, image)
    client.patch(f"/api/regions/{first['id']}", json={"source_text": "new"})
    response = rpc(client, "page_edit", mutation(image_id=image["id"], expected_versions=before["expected_versions"], delete=[first["region_key"]]))
    assert response["structuredContent"]["error"]["status"] == 409
    _, other = page(client)
    foreign = region(client, other)
    assert edit(client, image, delete=[foreign["id"]])["isError"]


def test_advance_queues_once_waits_and_honors_explicit_cancel(client, monkeypatch):
    project, image = page(client)
    run = begin(client, project)
    monkeypatch.setattr(task_manager, "dispatch", lambda identifier: None)
    args = mutation(run_id=run, image_id=image["id"], expected_versions=state(client, image)["expected_versions"])
    first = ok(rpc(client, "workflow_advance", args))
    task = first["task"]
    assert "payload" not in task
    assert ok(rpc(client, "workflow_advance", args))["task"]["id"] == task["id"]
    second = ok(rpc(client, "workflow_advance", mutation(run_id=run, image_id=image["id"], expected_versions=state(client, image)["expected_versions"])))
    assert second["next_steps"][0]["tool"] == "task_wait"
    with SessionLocal() as db:
        row = db.get(ProcessingTask, task["id"])
        assert row.payload["start_stage"] == "detection" and row.payload["end_stage"] == "ocr"
        assert row.payload["options"]["submitted_via"] == "mcp"
        assert db.scalar(select(func.count(ProcessingTask.id)).where(ProcessingTask.image_id == image["id"])) == 1
    ok(rpc(client, "task_cancel", mutation(task_id=task["id"], kind="processing")))
    status = ok(rpc(client, "workflow_status", {"run_id": run}))
    assert status["pages"][0]["execution_stage"] == "cancelled"
    assert rpc(client, "task_retry", mutation(task_id=task["id"], expected_versions=state(client, image)["expected_versions"]))["isError"]


def test_advance_rechecks_queued_work_at_atomic_admission(client, monkeypatch):
    from app.api import images
    project, image = page(client)
    run = begin(client, project)
    monkeypatch.setattr(task_manager, "dispatch", lambda identifier: None)
    original = images.process_image
    raced_task = []

    def race(image_id, payload, db):
        with SessionLocal() as other:
            task = ProcessingTask(project_id=project["id"], image_id=image_id, task_type="full", status="QUEUED",
                payload={"start_stage": "detection", "end_stage": "ocr", "options": {"workflow_run_id": run}})
            other.add(task)
            other.commit()
            raced_task.append(task.id)
        return original(image_id, payload, db)

    monkeypatch.setattr(images, "process_image", race)
    response = rpc(client, "workflow_advance", mutation(run_id=run, image_id=image["id"],
        expected_versions=state(client, image)["expected_versions"]))
    assert response["isError"] and response["structuredContent"]["error"]["status"] == 409
    with SessionLocal() as db:
        assert list(db.scalars(select(ProcessingTask.id).where(ProcessingTask.image_id == image["id"]))) == raced_task
    ok(rpc(client, "task_cancel", mutation(task_id=raced_task[0], kind="processing")))


def test_review_cannot_accept_a_changed_project_configuration(client):
    project, image = page(client)
    run = begin(client, project)
    client.patch(f"/api/projects/{project['id']}", json={"target_language": "en"})
    response = review(client, run, image, outcome="unverified", evidence_ids=[])
    assert response["isError"] and response["structuredContent"]["error"]["status"] == 409


def test_preflight_requires_selection_and_supports_keys_and_exclusions(client):
    project, image = page(client)
    text = region(client, image)
    art = region(client, image, bbox=[180, 20, 60, 80], region_type="sfx", translated_text="")
    run = begin(client, project)
    args = mutation(run_id=run, image_id=image["id"], phase="preflight", outcome="passed", note="Observed actual text and art",
        checks={name: {"status": "pass", "reason": "Observed"} for name in PREFLIGHT_CHECKS},
        evidence_ids=evidence(client, image, "preflight"), expected_versions=state(client, image)["expected_versions"],
        preserved=[{"region_id": art["region_key"], "reason": "Integrated SFX"}])
    assert rpc(client, "workflow_review_page", args)["isError"]
    ok(rpc(client, "workflow_review_page", {**args, "operation_id": str(uuid4()), "selection": {"mode": "all_eligible", "exclude": []}}))
    with SessionLocal() as db:
        row = db.scalar(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run))
        assert row.preflight["region_ids"] == [text["id"]]
        assert row.preflight["preserved"][0]["region_id"] == art["id"]


def test_review_records_issues_and_acceptance_moves_to_next_page(client):
    project, image = page(client)
    second = client.post(f"/api/projects/{project['id']}/images", files={"files": ("02.png", image_bytes())}).json()[0]
    render_empty(image)
    run = begin(client, project)
    response = ok(review(client, run, image, disposition="deliver_with_issues", issues=[{
        "category": "translation", "message": "Uncertain pronoun", "status": "uncertain"}]))
    assert response["issue_ids"]
    status = ok(rpc(client, "workflow_status", {"run_id": run}))
    assert status["current_page_id"] == second["id"]
    assert rpc(client, "workflow_export", mutation(run_id=run))["isError"]  # No silent missing page.
    assert review(client, run, image, outcome="passed")["isError"]
    ok(review(client, run, image, outcome="passed", issues=[{"issue_id": response["issue_ids"][0], "category": "translation",
        "message": "Verified context", "status": "resolved"}]))
    with SessionLocal() as db:
        assert db.get(AgentReviewIssue, response["issue_ids"][0]).status == "resolved"


def test_review_issue_and_hint_details_are_paginated_without_changing_page(client):
    project, image = page(client)
    render_empty(image)
    run = begin(client, project)
    ok(review(client, run, image, issues=[{"category": "translation", "message": f"Uncertain item {i}", "status": "uncertain"} for i in range(21)]))
    before = state(client, image)["version"]
    status = ok(rpc(client, "workflow_status", {"run_id": run, "include_details": True}))
    assert len(status["pages"][0]["issues"]) == 20 and status["pages"][0]["issue_count"] == 21
    next_call = status["pages"][0]["details_next_step"]
    rest = ok(rpc(client, next_call["tool"], next_call["arguments"]))
    assert len(rest["issues"]) == 1 and rest["details_next_offset"] is None
    assert rest["version"] == before == state(client, image)["version"]


def image_bytes():
    data = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(data, "PNG")
    return data.getvalue()


def test_eleven_page_status_is_small_and_recovers_existing_run(client):
    project, image = page(client)
    data = image_bytes()
    client.post(f"/api/projects/{project['id']}/images", files=[("files", (f"{i}.png", data)) for i in range(2, 12)])
    run = begin(client, project)
    with SessionLocal() as db:
        stored = db.get(MCPWorkflowRun, run)
        stored.workflow_version = "1.2.1"
        db.commit()
    response = rpc(client, "workflow_status", {"run_id": run})
    status = ok(response)
    assert status["page_count"] == 11 and len(status["pages"]) == 1
    assert len(json.dumps(status, ensure_ascii=False).encode()) <= 6*1024
    assert len(json.dumps(response, ensure_ascii=False).encode()) <= 6*1024
    text = json.loads(response["content"][0]["text"])
    assert text["next_steps"] == status["next_steps"] and text["pages"][0]["version"] == status["pages"][0]["version"]
    assert "quality_hints" not in status["pages"][0]
    found = ok(rpc(client, "project_list", {"project_id": project["id"]}))
    assert found["runs"][0]["run_id"] == run and found["page_count"] == 11
    assert found["next_offset"] == 10


def test_lost_operation_blocks_new_work_without_replaying(client):
    project, _ = page(client)
    run = begin(client, project)
    with SessionLocal() as db:
        db.add(MCPOperation(id="unknown-"+str(uuid4()), workflow_run_id=run, fingerprint="a"*64, status="UNKNOWN", result={}))
        db.commit()
    status = ok(rpc(client, "workflow_status", {"run_id": run}))
    assert status["next_steps"][0]["tool"] == "operation_status"


def test_locked_regions_and_frozen_repair_scope_cannot_be_bypassed(client):
    project, image = page(client)
    selected = region(client, image)
    other = region(client, image, bbox=[180, 20, 50, 80])
    run = begin(client, project, region_ids=[selected["id"]])
    assert edit(client, image, workflow_run_id=run, delete=[other["region_key"]])["isError"]
    client.patch(f"/api/regions/{selected['id']}", json={"locked": True})
    assert edit(client, image, update=[{"region_id": selected["region_key"], "patch": {"source_text": "blocked"}}])["isError"]
    assert rpc(client, "mask_paint", mutation(image_id=image["id"], region_id=selected["region_key"], mode="paint", shape="stroke",
        points=[[30, 40], [40, 50]], expected_versions=state(client, image)["expected_versions"]))["isError"]


def test_delete_false_detection_restores_artwork_without_repairing_other_masks(client):
    from app.storage import get_storage
    project, image = page(client)
    text = region(client, image)
    with SessionLocal() as db:
        target = db.get(ImagePage, image["id"])
        mask = Image.new("L", (300, 200), 0)
        mask.paste(255, (35, 50, 65, 70))
        directory = get_storage().page_dir(project["id"], image["id"])
        path = directory / "masks" / "false.png"
        mask.save(path)
        clean = Image.new("RGB", (300, 200), "white")
        clean.paste("red", (35, 50, 65, 70))
        clean_path = directory / "clean" / "false.png"
        clean.save(clean_path)
        target.clean_path = get_storage().relative(clean_path)
        db.get(TextRegion, text["id"]).pixel_mask_path = get_storage().relative(path)
        db.commit()
    changed = ok(edit(client, image, delete=[text["region_key"]]))
    assert changed["rebuild_task"] is None
    with SessionLocal() as db:
        with Image.open(get_storage().absolute(db.get(ImagePage, image["id"]).clean_path)) as clean:
            assert clean.getpixel((40, 60)) == (255, 255, 255)


def test_mask_edit_uses_numbered_reference_and_only_queues_necessary_repair(client, monkeypatch):
    project, image = page(client)
    text = region(client, image)
    run = begin(client, project)
    ok(review(client, run, image, phase="preflight", outcome="passed", region_ids=[text["region_key"]]))
    painted = ok(rpc(client, "mask_paint", mutation(image_id=image["id"], region_id=text["region_key"], workflow_run_id=run,
        mode="paint", shape="polygon", points=[[32, 45], [50, 45], [50, 70], [32, 70]],
        expected_versions=state(client, image)["expected_versions"])))
    assert painted["version"] == state(client, image)["version"]
    monkeypatch.setattr(task_manager, "dispatch", lambda identifier: None)
    task = ok(rpc(client, "workflow_advance", mutation(run_id=run, image_id=image["id"],
        expected_versions=state(client, image)["expected_versions"])))["task"]
    with SessionLocal() as db:
        payload = db.get(ProcessingTask, task["id"]).payload
        assert payload["start_stage"] == "inpainting" and payload["end_stage"] == "rendering"
        assert payload["force"] is False and payload["options"]["region_ids"] == [text["id"]]
    ok(rpc(client, "task_cancel", mutation(task_id=task["id"], kind="processing")))


def test_two_corrections_stop_and_continue_delivery(client):
    project, image = page(client)
    render_empty(image)
    run = begin(client, project)
    for number in range(2):
        ok(review(client, run, image, disposition="fix"))
        response = ok(rpc(client, "workflow_begin_correction", mutation(run_id=run, image_id=image["id"],
            expected_versions=state(client, image)["expected_versions"])))
        assert response["round"] == number+1
    assert review(client, run, image, disposition="fix")["isError"]
    ok(review(client, run, image, disposition="deliver_with_issues"))
    status = ok(rpc(client, "workflow_status", {"run_id": run}))
    assert status["current_page_id"] is None and status["next_steps"][0]["tool"] == "workflow_export"
    assert rpc(client, "workflow_begin_correction", mutation(run_id=run, image_id=image["id"],
        expected_versions=state(client, image)["expected_versions"]))["isError"]


def test_failed_task_retry_budget_and_receipt_link_survive_reconnect(client, monkeypatch):
    project, image = page(client)
    run = begin(client, project)
    monkeypatch.setattr(task_manager, "dispatch", lambda identifier: None)
    monkeypatch.setattr(task_manager, "promote", lambda identifier: None)
    task = ok(rpc(client, "workflow_advance", mutation(run_id=run, image_id=image["id"],
        expected_versions=state(client, image)["expected_versions"])))["task"]
    for attempt in range(3):
        with SessionLocal() as db:
            db.get(ProcessingTask, task["id"]).status = "FAILED"
            db.commit()
        args = mutation(task_id=task["id"], expected_versions=state(client, image)["expected_versions"])
        retried = rpc(client, "task_retry", args)
        assert bool(retried.get("isError")) == (attempt == 2)
        if attempt < 2:
            assert ok(retried)["id"] == task["id"]
            assert rpc(client, "task_retry", args) == retried
        with SessionLocal() as db:
            assert db.get(MCPOperation, args["operation_id"]).workflow_run_id == run


@pytest.mark.parametrize("raw", [{"body": {"force": True}}, {"start_stage": "inpainting"}, {"options": {"region_ids": []}}])
def test_advance_rejects_raw_pipeline_controls(client, raw):
    project, image = page(client)
    run = begin(client, project)
    response = rpc(client, "workflow_advance", mutation(run_id=run, image_id=image["id"],
        expected_versions=state(client, image)["expected_versions"], **raw))
    assert response["isError"]


def test_geometry_accepts_concavity_but_rejects_illegal_perspective(client):
    _, image = page(client)
    source = [[10, 10], [100, 10], [50, 40], [100, 100], [10, 100]]
    made = ok(edit(client, image, add=[{"polygon": source, "translated_bbox": [150, 20, 60, 100], "rotation": 12}]))
    row = state(client, image)["regions"][0]
    assert row["polygon"] == source and row["translated_bbox"] == [150, 20, 60, 100] and row["rotation"] == 12
    assert edit(client, image, update=[{"region_id": made["created"][0]["region_key"], "patch": {
        "translated_polygon": source, "perspective_warp": True}}])["isError"]


@pytest.mark.parametrize("kind", ["cbz", "translated"])
def test_unverified_delivery_has_correct_files_and_separate_report(client, kind):
    project, image = page(client)
    run = begin(client, project)
    ok(review(client, run, image, outcome="unverified", evidence_ids=[]))
    exported = ok(rpc(client, "workflow_export", mutation(run_id=run, kind=kind)))
    job = ok(rpc(client, "task_wait", {"task_id": exported["job"]["id"], "kind": "file", "seconds": 20}))
    assert job["status"] == "COMPLETED", job
    with ZipFile(io.BytesIO(client.get(job["download_path"]).content)) as archive:
        assert len(archive.namelist()) == 1 and archive.namelist()[0].endswith(".png")
        assert all("review" not in name for name in archive.namelist())
    report = client.get(exported["report_downloads"]["json"]).json()
    assert not report["fully_verified"] and report["pages"][0]["review"]["outcome"] == "unverified"
    assert job["result"]["report_downloads"] == exported["report_downloads"]

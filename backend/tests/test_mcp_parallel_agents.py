"""Concurrent native-client workers share one run without sharing page ownership."""
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from app.core.database import SessionLocal
from app.models import EditorHistoryEntry, ImagePage, MCPOperation, MCPWorkflowPage, MCPWorkflowRun, ProcessingTask
from app.models.entities import utcnow
from app.pipeline.processor import PipelineProcessor
from app.services import mcp_workflows
from app.services.base import DetectionResult, OCRResult
from app.services.translation.providers import OpenAICompatibleTranslator
from app.tasks import task_manager
from PIL import Image
from sqlalchemy import func, select
from test_mcp import mutation, rpc
from test_mcp_batch_workflow import book
from test_mcp_native_workflow import planned_evidence
from test_mcp_workflows import ok, region, render_empty, review, state


@pytest.fixture(autouse=True)
def configured(client, monkeypatch):
    client.headers["Host"] = "localhost"
    monkeypatch.setattr(mcp_workflows, "blockers", lambda *a, **kw: [])
    yield
    with SessionLocal() as db:
        for row in db.scalars(select(ProcessingTask).where(ProcessingTask.status.in_(["QUEUED", "RUNNING", "PAUSED"]))):
            if not task_manager.is_active(row.id):
                row.status = "CANCELLED"
        db.commit()


def prepare(client, count=2, *, rendered=False, regions=False):
    project, images = book(client, count)
    for image in images:
        if regions:
            region(client, image)
        with SessionLocal() as db:
            db.get(ImagePage, image["id"]).status = "OCR_DONE"
            db.commit()
        if rendered:
            render_empty(image)
    started = ok(rpc(client, "workflow_start", mutation(project_id=project["id"], coordination_mode="parallel")))
    return started, images


def claim(client, run, worker, image=None, **extra):
    return rpc(client, "workflow_advance", mutation(run_id=run["run_id"], action="claim", worker_id=worker,
        **({"image_id": image["id"]} if image else {}), **extra))


def keys(lease):
    return {"worker_id": lease["assignment"]["worker_id"], "assignment_token": lease["assignment"]["token"]}


def record(client, run, image, lease, *, phase="preflight", outcome="passed", **extra):
    evidence, _ = planned_evidence(client, image, phase)
    return review(client, run["run_id"], image, phase=phase, outcome=outcome, evidence_ids=evidence, **keys(lease), **extra)


def release(client, run, image, lease):
    return rpc(client, "workflow_advance", mutation(run_id=run["run_id"], action="release", image_id=image["id"], **keys(lease)))


def correct(client, run, image, lease):
    return rpc(client, "workflow_begin_correction", mutation(run_id=run["run_id"], image_id=image["id"],
        expected_versions=state(client, image)["expected_versions"], **keys(lease)))


def edit(client, run, image, lease, **changes):
    return rpc(client, "page_edit", mutation(workflow_run_id=run["run_id"], image_id=image["id"],
        expected_versions=state(client, image)["expected_versions"], **keys(lease), **changes))


def test_roles_fixed_tool_budget_and_capability_disclosure(client):
    main = ok(rpc(client, "workflow_get"))
    worker = ok(rpc(client, "workflow_get", {"role": "worker"}))
    assert main["version"] == worker["version"] == "2.2.0"
    assert len(json.dumps(worker)) < len(json.dumps(main)) < 8500
    assert "coordinator" in worker["instructions"] and "15 minutes" in worker["instructions"]
    capabilities = ok(rpc(client, "mcp_status"))["connection_capabilities"]
    assert capabilities["parallel_page_claims"] and capabilities["child_image_viewing"] == "unknown"


def test_parallel_default_status_remains_compact_for_eleven_pages(client):
    run, _ = prepare(client, 11)
    response = rpc(client, "workflow_status", {"run_id":run["run_id"]})
    assert len(json.dumps(response, ensure_ascii=False).encode()) <= 6*1024
    assert ok(response)["coordination"]["claimable"] == 11


@pytest.mark.parametrize("same_page", [True, False])
def test_simultaneous_claims_never_double_assign_a_page(client, same_page):
    run, images = prepare(client, 2)
    barrier = Barrier(2)
    def take(worker):
        barrier.wait(timeout=10)
        return ok(claim(client, run, worker, images[0] if same_page else None))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(take, ["reader-a", "reader-b"]))
    assigned = [value for value in results if value["assignment"]]
    assert len(assigned) == (1 if same_page else 2)
    assert len({value["image_id"] for value in assigned}) == len(assigned)
    assert len({value["assignment"]["token"] for value in assigned}) == len(assigned)


def test_claim_retry_and_reconnect_return_same_ownership_without_resetting_rounds(client):
    run, images = prepare(client)
    args = mutation(run_id=run["run_id"], action="claim", worker_id="reader")
    first = ok(rpc(client, "workflow_advance", args))
    assert ok(rpc(client, "workflow_advance", args)) == first
    resumed = ok(claim(client, run, "reader"))
    assert resumed["assignment"] == first["assignment"]
    assert claim(client, run, "reader", images[1])["isError"]
    status = ok(rpc(client, "workflow_status", {"run_id": run["run_id"], **keys(first)}))
    assert len(status["pages"]) == 1 and status["pages"][0]["image_id"] == first["image_id"]
    hidden = ok(rpc(client, "workflow_status", {"run_id": run["run_id"], "worker_id": "reader"}))
    assert not hidden["pages"] and hidden["coordination"]["credential_required"]
    coordinator = ok(rpc(client, "workflow_status", {"run_id": run["run_id"]}))
    assert first["assignment"]["token"] not in json.dumps(coordinator)


def test_coordinator_waits_for_existing_workers_instead_of_resending_review_instructions(client):
    run, _ = prepare(client, 2)
    ok(claim(client, run, "reader-a"))
    ok(claim(client, run, "reader-b"))
    status = ok(rpc(client, "workflow_status", {"run_id":run["run_id"]}))
    assert status["coordination"]["claimable"] == 0 and status["coordination"]["claimed"] == 2
    assert not status["next_steps"]
    assert "existing client-native workers" in status["coordination"]["next_instruction"]


@pytest.mark.parametrize("disposition", ["defer", "fix"])
def test_unresolved_final_review_retains_ownership_without_a_live_claim(client, disposition):
    run, images = prepare(client, 1, rendered=True)
    image = images[0]
    lease = ok(claim(client, run, "reader"))
    ok(record(client, run, image, lease, phase="final", outcome="issues", disposition=disposition))
    assert ok(rpc(client, "workflow_status", {"run_id":run["run_id"]}))["pages"][0]["execution_stage"] == "correction_available"
    before = state(client, image)
    changed = rpc(client, "page_edit", mutation(image_id=image["id"], expected_versions=before["expected_versions"],
        add=[{"bbox":[10,10,30,30], "source_text":"Unclaimed", "translated_text":"Unclaimed"}]))
    assert changed["isError"] and state(client, image)["version"] == before["version"]
    assert rpc(client, "workflow_start", mutation(project_id=image["project_id"], coordination_mode="parallel"))["isError"]
    lease = ok(claim(client, run, "reader"))
    ok(correct(client, run, image, lease))
    assert ok(edit(client, run, image, lease, add=[{"bbox":[10,10,30,30]}]))["created"]


def test_coordinator_delegates_ready_correction_while_another_page_reprocesses(client, monkeypatch):
    run, images = prepare(client, 2, regions=True)
    for index, image in enumerate(images):
        lease = ok(claim(client, run, f"reader-{index}", image))
        ok(record(client, run, image, lease, outcome="issues", disposition="defer"))
    lease = ok(claim(client, run, "reader-a", images[0]))
    ok(correct(client, run, images[0], lease))
    ok(edit(client, run, images[0], lease, update=[{"region_id":"R001", "patch":{"source_text":"Corrected"}}]))
    ok(release(client, run, images[0], lease))
    monkeypatch.setattr(task_manager, "dispatch", lambda _: None)
    ok(rpc(client, "workflow_advance", mutation(run_id=run["run_id"], image_id=images[0]["id"],
        coordinator_token=run["coordinator_token"], expected_versions=state(client, images[0])["expected_versions"])))
    progress = ok(rpc(client, "workflow_status", {"run_id":run["run_id"]}))
    assert progress["coordination"]["claimable"] == 1
    assert progress["next_steps"][0]["tool"] == "workflow_get"
    other = ok(claim(client, run, "reader-b"))
    assert other["image_id"] == images[1]["id"]
    assert ok(correct(client, run, images[1], other))["round"] == 1


def changed_configuration(client, run, image):
    current = client.get(f"/api/projects/{image['project_id']}").json()
    assert client.patch(f"/api/projects/{image['project_id']}", json={
        "target_language":"fr" if current["target_language"] != "fr" else "de"}).status_code == 200
    return ok(rpc(client, "workflow_status", {"run_id":run["run_id"]}))


def test_configuration_change_allows_release_and_explicit_same_run_recovery(client):
    run, images = prepare(client, 1, rendered=True, regions=True)
    image = images[0]
    lease = ok(claim(client, run, "reader"))
    ok(record(client, run, image, lease, phase="final", outcome="issues", disposition="defer"))
    lease = ok(claim(client, run, "reader"))
    ok(correct(client, run, image, lease))
    before = state(client, image)
    changed_configuration(client, run, image)
    worker = ok(rpc(client, "workflow_status", {"run_id":run["run_id"], **keys(lease)}))
    assert worker["next_steps"][0]["arguments"]["action"] == "release"
    ok(release(client, run, image, lease))
    progress = ok(rpc(client, "workflow_status", {"run_id":run["run_id"]}))
    action = progress["next_steps"][0]
    assert action["arguments"]["action"] == "accept_configuration" and not action["automatic"]
    args = mutation(**action["arguments"], coordinator_token=run["coordinator_token"])
    assert rpc(client, "workflow_advance", {**args,"coordinator_token":"wrong","operation_id":"wrong-config-token"})["isError"]
    result = ok(rpc(client, "workflow_advance", args))
    assert result["reviews_invalidated"] and ok(rpc(client, "workflow_advance", args)) == result
    after = state(client, image)
    assert after["version"] == before["version"] and after["regions"] == before["regions"]
    with SessionLocal() as db:
        stored = db.get(MCPWorkflowRun, run["run_id"])
        receipt = db.scalar(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run["run_id"]))
        assert stored.options["configuration_history"][-1]["pages"][0]["final_review"]["outcome"] == "issues"
        assert not receipt.preflight and not receipt.final_review
        assert receipt.correction_rounds == 1 and receipt.correction_open
    assert not ok(rpc(client, "workflow_status", {"run_id":run["run_id"]}))["configuration_stale"]
    assert rpc(client, "workflow_export", mutation(run_id=run["run_id"], coordinator_token=run["coordinator_token"]))["isError"]
    assert edit(client, run, image, lease, add=[{"bbox":[10,10,30,30]}])["isError"]
    assert ok(claim(client, run, "returning-reader"))["assignment"]


@pytest.mark.parametrize("mode", ["single", "parallel"])
def test_configuration_recovery_requires_new_review_then_delivers_same_run(client, mode):
    project, images = book(client, 2)
    for image in images:
        render_empty(image)
    run = ok(rpc(client, "workflow_start", mutation(project_id=project["id"], coordination_mode=mode)))
    for image in images:
        if mode == "parallel":
            lease = ok(claim(client, run, "reader", image))
            ok(record(client, run, image, lease, phase="final", outcome="unverified"))
        else:
            ok(review(client, run["run_id"], image, phase="final", outcome="unverified"))
    progress = changed_configuration(client, run, images[0])
    action = progress["next_steps"][0]
    credentials = {"coordinator_token": run["coordinator_token"]} if mode == "parallel" else {}
    arguments = mutation(**action["arguments"], **credentials)
    assert rpc(client, "workflow_advance", {**arguments, "operation_id":"partial-config-versions",
        "expected_versions":state(client, images[0])["expected_versions"]})["isError"]
    recovered = ok(rpc(client, "workflow_advance", arguments))
    with SessionLocal() as db:
        operation = db.get(MCPOperation, arguments["operation_id"])
        operation.status = "UNKNOWN"
        operation.result = {"_request":{"tool":"workflow_advance", "action":"accept_configuration"}, "_coordination":recovered}
        db.commit()
    assert ok(rpc(client, "operation_status", {"operation_id":arguments["operation_id"]}))["result"] == recovered
    assert rpc(client, "workflow_export", mutation(run_id=run["run_id"], **credentials))["isError"]
    for image in images:
        if mode == "parallel":
            lease = ok(claim(client, run, "reader", image))
            ok(record(client, run, image, lease, phase="final", outcome="unverified"))
        else:
            ok(review(client, run["run_id"], image, phase="final", outcome="unverified"))
    output = ok(rpc(client, "workflow_export", mutation(run_id=run["run_id"], **credentials)))
    done = ok(rpc(client, "task_wait", {"task_id":output["job"]["id"], "kind":"file", "seconds":20}))
    assert done["status"] == "COMPLETED", done
    report = client.get(output["report_downloads"]["json"]).json()
    assert report["run_id"] == run["run_id"] and len(report["pages"]) == 2 and not report["fully_verified"]
    with SessionLocal() as db:
        assert len(db.get(MCPWorkflowRun, run["run_id"]).options["configuration_history"]) == 1


def test_uncommitted_configuration_acceptance_does_not_replay_or_change_the_run(client):
    run, images = prepare(client, 1)
    changed_configuration(client, run, images[0])
    with SessionLocal() as db:
        db.add(MCPOperation(id="config-not-committed", fingerprint="b"*64, workflow_run_id=run["run_id"], status="UNKNOWN",
            result={"_request":{"tool":"workflow_advance", "action":"accept_configuration"}}))
        db.commit()
    result = ok(rpc(client, "operation_status", {"operation_id":"config-not-committed"}))
    assert result["status"] == "COMPLETED" and not result["result"]["applied"]
    assert ok(rpc(client, "workflow_status", {"run_id":run["run_id"]}))["configuration_stale"]


@pytest.mark.parametrize("blocker", ["lease", "task", "operation", "page", "configuration"])
def test_configuration_recovery_rechecks_ownership_work_and_versions(client, blocker):
    run, images = prepare(client, 1)
    image = images[0]
    if blocker == "lease":
        lease = ok(claim(client, run, "reader"))
    progress = changed_configuration(client, run, image)
    versions = state(client, image)["expected_versions"]
    args = mutation(run_id=run["run_id"], action="accept_configuration", coordinator_token=run["coordinator_token"],
        configuration_version=progress["configuration_change"]["version"], expected_versions=versions)
    if blocker == "task":
        with SessionLocal() as db:
            db.add(ProcessingTask(project_id=image["project_id"], image_id=image["id"], task_type="full", status="QUEUED"))
            db.commit()
    elif blocker == "operation":
        with SessionLocal() as db:
            db.add(MCPOperation(id="unknown-before-config", fingerprint="a"*64, workflow_run_id=run["run_id"], status="UNKNOWN"))
            db.commit()
    elif blocker == "page":
        region(client, image)
    elif blocker == "configuration":
        client.patch(f"/api/projects/{image['project_id']}", json={"target_language":"ja"})
    response = rpc(client, "workflow_advance", args)
    assert response["isError"] and response["structuredContent"]["error"]["code"] == "conflict"
    with SessionLocal() as db:
        assert "configuration_history" not in db.get(MCPWorkflowRun, run["run_id"]).options
    if blocker == "lease":
        ok(release(client, run, image, lease))


def test_coordinator_queues_released_corrections_without_waiting_for_held_page(client):
    run, images = prepare(client, 2, regions=True)
    for index, image in enumerate(images):
        lease = ok(claim(client, run, f"preflight-{index}", image))
        ok(record(client, run, image, lease))
        queued = ok(rpc(client, "workflow_advance", mutation(run_id=run["run_id"], image_id=image["id"],
            coordinator_token=run["coordinator_token"], expected_versions=state(client, image)["expected_versions"])))
        finished = ok(rpc(client, "task_wait", {"task_id":queued["task"]["id"],"kind":"processing","seconds":20}))
        assert finished["status"] == "COMPLETED", finished
    leases = []
    for index, image in enumerate(images):
        lease = ok(claim(client, run, f"reader-{index}", image))
        ok(record(client, run, image, lease, phase="final", outcome="issues", disposition="defer"))
        lease = ok(claim(client, run, f"reader-{index}", image))
        ok(correct(client, run, image, lease))
        ok(edit(client, run, image, lease, update=[{"region_id":"R001","patch":{"rotation":10}}]))
        leases.append(lease)
    ok(release(client, run, images[1], leases[1]))
    status = ok(rpc(client, "workflow_status", {"run_id":run["run_id"]}))
    assert status["next_steps"][0]["tool"] == "workflow_advance"
    assert status["next_steps"][0]["arguments"]["image_ids"] == [images[1]["id"]]


def test_renew_release_and_status_preserve_content_version(client):
    run, images = prepare(client, 1)
    image = images[0]
    before = state(client, image)["version"]
    lease = ok(claim(client, run, "reader"))
    status_args = {"run_id": run["run_id"], **keys(lease)}
    first = ok(rpc(client, "workflow_status", status_args))["pages"][0]["assignment"]
    second = ok(rpc(client, "workflow_status", status_args))["pages"][0]["assignment"]
    assert first == second  # Reading status never extends a lease.
    renewed = ok(rpc(client, "workflow_advance", mutation(action="renew", image_id=image["id"], **status_args)))
    assert renewed["assignment"]["token"] == first["token"]
    assert renewed["assignment"]["expires_at"] >= first["expires_at"]
    assert ok(release(client, run, image, lease))["released"]
    new = ok(claim(client, run, "other"))
    assert new["assignment"]["token"] != lease["assignment"]["token"]
    assert state(client, image)["version"] == before


def test_non_ascii_wrong_credential_returns_conflict_not_unknown_operation(client):
    run, images = prepare(client, 1)
    lease = ok(claim(client, run, "reader"))
    args = mutation(run_id=run["run_id"], action="renew", image_id=images[0]["id"],
        worker_id="reader", assignment_token="错误的凭据")
    response = rpc(client, "workflow_advance", args)
    assert response["isError"] and response["structuredContent"]["error"]["code"] == "conflict"
    with SessionLocal() as db:
        assert db.get(MCPOperation, args["operation_id"]).status == "COMPLETED"
    assert ok(rpc(client, "workflow_status", {"run_id":run["run_id"], **keys(lease)}))["pages"]


@pytest.mark.parametrize("action", ["edit", "review", "renew", "release", "correction"])
def test_expired_worker_cannot_write_after_reassignment(client, action):
    run, images = prepare(client, 1, regions=True)
    image = images[0]
    old = ok(claim(client, run, "old"))
    with SessionLocal() as db:
        row = db.scalar(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run["run_id"]))
        row.assignment = {**row.assignment, "expires_at": (utcnow()-timedelta(seconds=1)).isoformat()}
        db.commit()
    new = ok(claim(client, run, "new"))
    assert new["image_id"] == old["image_id"]
    before = state(client, image)["version"]
    if action == "edit":
        response = edit(client, run, image, old, update=[{"region_id": "R001", "patch": {"translated_text": "stale"}}])
    elif action == "review":
        response = record(client, run, image, old)
    elif action == "correction":
        response = correct(client, run, image, old)
    else:
        response = rpc(client, "workflow_advance", mutation(run_id=run["run_id"], action=action, image_id=image["id"], **keys(old)))
    assert response["isError"]
    assert state(client, image)["version"] == before


@pytest.mark.parametrize("omit", ["run", "token", "worker"])
def test_edit_cannot_bypass_ownership_by_omitting_fields(client, omit):
    run, images = prepare(client, 1, regions=True)
    lease = ok(claim(client, run, "reader"))
    args = mutation(image_id=images[0]["id"], workflow_run_id=run["run_id"], **keys(lease),
        expected_versions=state(client, images[0])["expected_versions"], update=[{"region_id": "R001", "patch": {"translated_text": "bad"}}])
    del args[{"run": "workflow_run_id", "token": "assignment_token", "worker": "worker_id"}[omit]]
    assert rpc(client, "page_edit", args)["isError"]


def test_web_edit_allowed_but_invalidates_worker_evidence(client):
    run, images = prepare(client, 1, regions=True)
    image = images[0]
    lease = ok(claim(client, run, "reader"))
    before = state(client, image)
    evidence, _ = planned_evidence(client, image, "preflight")
    identifier = before["regions"][0]["id"]
    changed = client.patch(f"/api/regions/{identifier}", json={"translated_text": "Web corrected"})
    assert changed.status_code == 200
    assert state(client, image)["version"] != before["version"]
    assert review(client, run["run_id"], image, phase="preflight", outcome="passed",
        evidence_ids=evidence, **keys(lease))["isError"]
    ok(record(client, run, image, lease))


@pytest.mark.parametrize("action", ["claim", "renew", "release"])
def test_unknown_lease_operation_recovers_exact_committed_receipt(client, action):
    run, images = prepare(client, 1)
    lease = ok(claim(client, run, "reader"))
    args = mutation(run_id=run["run_id"], image_id=images[0]["id"], action=action, **keys(lease))
    response = ok(rpc(client, "workflow_advance", args))
    with SessionLocal() as db:
        row = db.get(MCPOperation, args["operation_id"])
        row.status = "UNKNOWN"
        row.result = {"_request": {"tool": "workflow_advance", "action": action}, "_coordination": response}
        db.commit()
    recovered = ok(rpc(client, "operation_status", {"operation_id": args["operation_id"]}))
    assert recovered["status"] == "COMPLETED" and recovered["result"] == response
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ProcessingTask).where(ProcessingTask.project_id == images[0]["project_id"])) == 0


def test_interrupted_start_recovers_coordinator_credential_without_creating_another_run(client):
    run, _ = prepare(client, 1)
    with SessionLocal() as db:
        stored = db.get(MCPWorkflowRun, run["run_id"])
        identifier = stored.options["coordinator_operation_id"]
        op = db.get(MCPOperation, identifier)
        op.status = "UNKNOWN"
        op.result = {"_request":{"tool":"workflow_start"},"_coordination":run}
        db.commit()
    recovered = ok(rpc(client, "operation_status", {"operation_id":identifier}))
    assert recovered["result"] == run
    assert recovered["result"]["coordinator_token"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(MCPWorkflowRun).where(MCPWorkflowRun.id == run["run_id"])) == 1


def test_uncommitted_claim_is_reported_without_replaying_work(client):
    run, _ = prepare(client, 1)
    with SessionLocal() as db:
        db.add(MCPOperation(id="uncommitted-claim", fingerprint="f"*64, workflow_run_id=run["run_id"], status="UNKNOWN",
            result={"_request":{"tool":"workflow_advance","action":"claim"}}))
        db.commit()
    recovered = ok(rpc(client, "operation_status", {"operation_id":"uncommitted-claim"}))
    assert recovered["status"] == "COMPLETED" and recovered["result"]["applied"] is False
    lease = ok(claim(client, run, "reader"))
    assert lease["assignment"]


def test_first_batch_allows_review_but_blocks_correction_until_it_ends(client):
    run, images = prepare(client, 2, regions=True)
    with SessionLocal() as db:
        task = ProcessingTask(project_id=images[0]["project_id"], image_id=images[0]["id"], task_type="full", status="RUNNING",
            payload={"start_stage": "ocr", "end_stage": "translation", "options": {"workflow_run_id": run["run_id"]}})
        db.add(task)
        db.commit()
        identifier = task.id
    lease = ok(claim(client, run, "reader"))
    assert lease["image_id"] == images[1]["id"]
    ok(record(client, run, images[1], lease, outcome="issues", disposition="defer"))
    assert ok(claim(client, run, "reader"))["assignment"] is None
    with SessionLocal() as db:
        db.get(ProcessingTask, identifier).status = "COMPLETED"
        db.commit()
    lease = ok(claim(client, run, "reader", images[1]))
    assert lease["assignment"]
    ok(correct(client, run, images[1], lease))


def test_one_page_reprocessing_does_not_block_another_pages_correction(client, monkeypatch):
    run, images = prepare(client, 2, regions=True)
    for index, image in enumerate(images):
        lease = ok(claim(client, run, f"reader-{index}", image))
        ok(record(client, run, image, lease, outcome="issues", disposition="defer"))
    first = ok(claim(client, run, "reader-a", images[0]))
    ok(correct(client, run, images[0], first))
    ok(edit(client, run, images[0], first, update=[{"region_id": "R001", "patch": {"source_text": "Corrected OCR"}}]))
    ok(release(client, run, images[0], first))
    monkeypatch.setattr(task_manager, "dispatch", lambda _: None)
    queued = ok(rpc(client, "workflow_advance", mutation(run_id=run["run_id"], image_id=images[0]["id"],
        coordinator_token=run["coordinator_token"], expected_versions=state(client, images[0])["expected_versions"])))
    assert queued["task"]["status"] == "QUEUED"
    second = ok(claim(client, run, "reader-b", images[1]))
    ok(correct(client, run, images[1], second))
    ok(edit(client, run, images[1], second, update=[{"region_id": "R001", "patch": {"translated_text": "Local correction"}}]))
    assert state(client, images[1])["regions"][0]["translated_text"] == "Local correction"


def test_output_wave_finishes_before_final_corrections_then_pages_are_independent(client):
    run, images = prepare(client, 2)
    for index, image in enumerate(images):
        lease = ok(claim(client, run, f"preflight-{index}", image))
        ok(record(client, run, image, lease))
    render_empty(images[0])
    with SessionLocal() as db:
        stored = db.get(MCPWorkflowRun, run["run_id"])
        stored.options = {**stored.options, "batch_phase":"output", "output_started":True}
        db.commit()
    lease = ok(claim(client, run, "final-a", images[0]))
    ok(record(client, run, images[0], lease, phase="final", outcome="issues", disposition="defer"))
    # Another approved page has not even been queued for its first output yet.
    assert ok(claim(client, run, "final-a", images[0]))["assignment"] is None
    render_empty(images[1])
    lease = ok(claim(client, run, "final-a", images[0]))
    ok(correct(client, run, images[0], lease))
    ok(release(client, run, images[0], lease))
    with SessionLocal() as db:
        db.add(ProcessingTask(project_id=images[0]["project_id"], image_id=images[0]["id"], task_type="full", status="QUEUED",
            payload={"start_stage":"inpainting","end_stage":"rendering","options":{"workflow_run_id":run["run_id"]}}))
        db.commit()
    lease = ok(claim(client, run, "final-b", images[1]))
    ok(record(client, run, images[1], lease, phase="final", outcome="issues", disposition="defer"))
    lease = ok(claim(client, run, "final-b", images[1]))
    assert ok(correct(client, run, images[1], lease))["round"] == 1


def test_worker_cannot_process_export_cancel_or_create_overlapping_run(client):
    run, images = prepare(client, 1)
    lease = ok(claim(client, run, "reader"))
    assert rpc(client, "workflow_advance", mutation(run_id=run["run_id"], image_id=images[0]["id"],
        expected_versions=state(client, images[0])["expected_versions"], **keys(lease)))["isError"]
    assert rpc(client, "workflow_export", mutation(run_id=run["run_id"]))["isError"]
    assert rpc(client, "workflow_start", mutation(project_id=images[0]["project_id"], coordination_mode="parallel"))["isError"]
    with SessionLocal() as db:
        task = ProcessingTask(project_id=images[0]["project_id"], image_id=images[0]["id"], task_type="full", status="QUEUED",
            payload={"options": {"workflow_run_id": run["run_id"]}})
        db.add(task)
        db.commit()
        identifier = task.id
    assert rpc(client, "task_cancel", mutation(task_id=identifier, kind="processing"))["isError"]


def test_review_releases_lease_and_persists_worker_attribution(client):
    run, images = prepare(client, 1)
    lease = ok(claim(client, run, "reviewer-1"))
    before = state(client, images[0])["version"]
    ok(record(client, run, images[0], lease))
    with SessionLocal() as db:
        receipt = db.scalar(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run["run_id"]))
        assert receipt.assignment == {}
        assert receipt.preflight["worker_id"] == "reviewer-1"
        assert receipt.preflight["claim_to_review_seconds"] >= 0
    assert state(client, images[0])["version"] == before


def test_worker_edits_require_a_recorded_correction_round_and_masks_use_same_lease(client):
    run, images = prepare(client, 1, regions=True)
    image = images[0]
    lease = ok(claim(client, run, "reader"))
    assert edit(client, run, image, lease, update=[{"region_id":"R001","patch":{"translated_text":"Too early"}}])["isError"]
    ok(record(client, run, image, lease, outcome="issues", disposition="defer"))
    lease = ok(claim(client, run, "reader"))
    ok(correct(client, run, image, lease))
    args = mutation(image_id=image["id"], workflow_run_id=run["run_id"], region_id="R001", mode="paint", shape="stroke",
        points=[[40,50],[60,50]], width=5, expected_versions=state(client, image)["expected_versions"], **keys(lease))
    painted = ok(rpc(client, "mask_paint", args))
    assert painted["changed"] and painted["version"] != lease["version"]
    wrong = {**args, "operation_id":"mask-wrong-worker", "worker_id":"somebody-else", "expected_versions": state(client, image)["expected_versions"]}
    assert rpc(client, "mask_paint", wrong)["isError"]
    wrong.pop("workflow_run_id")
    wrong["operation_id"] = "mask-without-workflow"
    assert rpc(client, "mask_paint", wrong)["isError"]


def test_lease_expiring_during_edit_preparation_cannot_publish_or_append_history(client, monkeypatch):
    from app.services import region_edits
    run, images = prepare(client, 1, regions=True)
    image = images[0]
    lease = ok(claim(client, run, "reader"))
    ok(record(client, run, image, lease, outcome="issues", disposition="defer"))
    lease = ok(claim(client, run, "reader"))
    ok(correct(client, run, image, lease))
    before = state(client, image)["version"]
    with SessionLocal() as db:
        history_count = db.scalar(select(func.count()).select_from(EditorHistoryEntry).where(EditorHistoryEntry.image_id == image["id"]))
    original = region_edits._archive
    expired = False
    def expire_after_capture(*args, **kwargs):
        nonlocal expired
        result = original(*args, **kwargs)
        if not expired:
            expired = True
            with SessionLocal() as db:
                receipt = db.scalar(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run["run_id"]))
                receipt.assignment = {**receipt.assignment, "expires_at": (utcnow()-timedelta(seconds=1)).isoformat()}
                db.commit()
        return result
    monkeypatch.setattr(region_edits, "_archive", expire_after_capture)
    response = edit(client, run, image, lease, update=[{"region_id":"R001","patch":{"translated_text":"Must not publish"}}])
    assert response["isError"] and expired
    assert state(client, image)["version"] == before
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(EditorHistoryEntry).where(EditorHistoryEntry.image_id == image["id"])) == history_count


def test_parallel_agent_translation_stays_coordinator_owned_and_text_only(client):
    project, images = book(client, 1)
    image = images[0]
    region(client, image, source_text="Recognized", translated_text="")
    run = ok(rpc(client, "workflow_start", mutation(project_id=project["id"], coordination_mode="parallel", translation_mode="agent")))
    assert ok(claim(client, run, "reader"))["assignment"] is None
    args = mutation(image_id=image["id"], workflow_run_id=run["run_id"], coordinator_token=run["coordinator_token"],
        expected_versions=state(client, image)["expected_versions"], update=[{"region_id":"R001","patch":{"translated_text":"Coordinator translation"}}])
    ok(rpc(client, "page_edit", args))
    bad = {**args, "operation_id":"coordinator-geometry-rejected", "expected_versions": state(client, image)["expected_versions"],
        "update":[{"region_id":"R001","patch":{"bbox":[1,2,30,40]}}]}
    assert rpc(client, "page_edit", bad)["isError"]
    assert ok(claim(client, run, "reader"))["assignment"]


def test_new_run_supersedes_completed_parallel_run_without_resurrecting_old_ownership(client):
    run, images = prepare(client, 1, rendered=True)
    image = images[0]
    lease = ok(claim(client, run, "old-worker"))
    ok(record(client, run, image, lease, phase="final", outcome="unverified"))
    newer = ok(rpc(client, "workflow_start", mutation(project_id=image["project_id"], coordination_mode="parallel")))
    lease = ok(claim(client, newer, "new-worker"))
    ok(record(client, newer, image, lease, phase="final", outcome="issues", disposition="defer"))
    lease = ok(claim(client, newer, "new-worker"))
    ok(correct(client, newer, image, lease))
    ok(edit(client, newer, image, lease, add=[{"bbox":[10,20,30,40],"source_text":"Human","translated_text":"Translated"}]))
    # Content changed, so the old receipt is now stale. It must stay superseded.
    assert claim(client, run, "old-worker")["isError"]
    assert ok(rpc(client, "workflow_status", {"run_id":run["run_id"]}))["coordination"]["superseded_by"] == newer["run_id"]


def test_export_closes_claim_scope_and_explicit_resume_keeps_rounds(client):
    run, images = prepare(client, 1, rendered=True)
    image = images[0]
    lease = ok(claim(client, run, "reader"))
    ok(record(client, run, image, lease, phase="final", outcome="unverified"))
    ok(rpc(client, "workflow_export", mutation(run_id=run["run_id"], coordinator_token=run["coordinator_token"])))
    with SessionLocal() as db:
        stored = db.get(MCPWorkflowRun, run["run_id"])
        assert stored.options["coordination_closed"]
        receipt = db.scalar(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run["run_id"]))
        receipt.correction_rounds = 1
        db.commit()
    # A browser edit is permitted after export; resuming explicitly re-opens coordination.
    region(client, image, source_text="New text", translated_text="New translation")
    resumed = ok(claim(client, run, "returning-reader"))
    assert resumed["assignment"]
    with SessionLocal() as db:
        assert not db.get(MCPWorkflowRun, run["run_id"]).options["coordination_closed"]
        assert db.scalar(select(MCPWorkflowPage).where(MCPWorkflowPage.run_id == run["run_id"])).correction_rounds == 1


def test_two_round_budget_survives_worker_handoff(client):
    run, images = prepare(client, 1, rendered=True)
    image = images[0]
    with SessionLocal() as db:
        row = db.get(MCPWorkflowRun, run["run_id"])
        row.options = {**row.options, "output_started": True}
        db.commit()
    for index in range(3):
        lease = ok(claim(client, run, f"reviewer-{index}"))
        ok(record(client, run, image, lease, phase="final", outcome="issues", disposition="defer"))
        lease = ok(claim(client, run, f"reviewer-{index}"))
        opened = correct(client, run, image, lease)
        if index < 2:
            assert ok(opened)["round"] == index+1
            ok(record(client, run, image, lease, phase="final", outcome="issues", disposition="defer"))
        else:
            assert opened["isError"]
            ok(record(client, run, image, lease, phase="final", outcome="issues", disposition="deliver_with_issues"))


def test_fifty_page_parallel_translation_review_and_export_without_duplicate_tasks(client, monkeypatch):
    """Real pipeline/history/export; deterministic models, not visual reasoning."""
    project, images = book(client, 50)
    class Translator(OpenAICompatibleTranslator):
        async def translate_regions(self, regions, **kwargs):
            return {identifier: "Hello" for identifier, _ in regions}

    def inpaint(source, mask, target, region_type):
        with Image.open(source) as picture:
            Image.new("RGB", picture.size, "white").save(target)
        return target

    providers = {"detection": SimpleNamespace(detect=lambda path: [DetectionResult(bbox=[20, 20, 120, 100],
        polygon=[[20,20],[140,20],[140,120],[20,120]], confidence=.99, orientation="horizontal")]),
        "ocr": SimpleNamespace(ensure_loaded=lambda: None, recognize=lambda *a, **kw: OCRResult("こんにちは", .99, "horizontal")),
        "translation": Translator({}), "inpainting": SimpleNamespace(inpaint=inpaint)}
    original = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda processor, kind, requested:
        (providers[kind], "parallel-fixture") if kind in providers else original(processor, kind, requested))
    run = ok(rpc(client, "workflow_start", mutation(project_id=project["id"], coordination_mode="parallel")))
    started = time.monotonic()

    def process_wave():
        tasks = []
        while True:
            status = ok(rpc(client, "workflow_status", {"run_id": run["run_id"]}))
            call = status["next_steps"][0]
            if call["tool"] != "workflow_advance":
                break
            accepted = ok(rpc(client, call["tool"], mutation(**call["arguments"], coordinator_token=run["coordinator_token"])))
            tasks.extend(accepted["tasks"])
        assert len(tasks) == 50 and len({task["image_id"] for task in tasks}) == 50
        for task in tasks:
            finished = ok(rpc(client, "task_wait", {"task_id": task["id"], "kind": "processing", "seconds": 20}))
            assert finished["status"] == "COMPLETED", finished
        return tasks

    def review_wave(phase):
        def worker(worker_id):
            visited = []
            while True:
                lease = ok(claim(client, run, worker_id))
                if not lease["assignment"]:
                    break
                image = {"id": lease["image_id"]}
                result = ok(record(client, run, image, lease, phase=phase,
                    outcome="passed" if phase == "preflight" else "unverified",
                    **({"selection": {"mode": "all_eligible"}} if phase == "preflight" else {})))
                assert result["remaining_rounds"] == 2
                visited.append(image["id"])
            return visited
        with ThreadPoolExecutor(max_workers=4) as pool:
            groups = list(pool.map(worker, [f"worker-{i}" for i in range(4)]))
        visited = [identifier for group in groups for identifier in group]
        assert len(visited) == len(set(visited)) == 50
        assert sum(bool(group) for group in groups) >= 2

    text_tasks = process_wave()
    with SessionLocal() as db:
        assert all(db.get(ImagePage, image["id"]).clean_path is None for image in images)
    review_wave("preflight")
    assert rpc(client, "workflow_export", mutation(run_id=run["run_id"], coordinator_token=run["coordinator_token"]))["isError"]
    output_tasks = process_wave()
    review_wave("final")
    job = ok(rpc(client, "workflow_export", mutation(run_id=run["run_id"], coordinator_token=run["coordinator_token"])))
    done = ok(rpc(client, "task_wait", {"task_id": job["job"]["id"], "kind": "file", "seconds": 20}))
    assert done["status"] == "COMPLETED", done
    with ZipFile(io.BytesIO(client.get(done["download_path"]).content)) as archive:
        assert archive.namelist() == [f"{number:04}.png" for number in range(1, 51)]
    report = client.get(job["report_downloads"]["json"]).json()
    assert len(report["pages"]) == 50 and not report["fully_verified"]
    assert all(page["review"]["worker_id"].startswith("worker-") for page in report["pages"])
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(ProcessingTask).where(ProcessingTask.project_id == project["id"])) == len(text_tasks)+len(output_tasks) == 100
    print(json.dumps({"fixture": "50-page-4-worker-protocol-only", "wall_seconds": round(time.monotonic()-started, 2),
        "task_count": 100, "visual_model_verified": False}))

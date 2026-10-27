"""Official workflow checkpoints exercised through the actual MCP transport."""
import io
from zipfile import ZipFile

import pytest
from app.core.database import SessionLocal
from app.models import ImagePage
from app.services import mcp_workflows
from app.services.mcp_workflow_definitions import CHECKS, PREFLIGHT_CHECKS
from PIL import Image
from test_mcp import mutation, page, rpc


@pytest.fixture(autouse=True)
def local_host(client):
    client.headers["Host"] = "localhost"


def ok(response):
    assert not response.get("isError"), response
    return response["structuredContent"]


def state(client, image):
    return ok(rpc(client, "page_state", {"image_id": image["id"]}))


def begin(client, project, *, legacy=True, **options):
    identifier = ok(rpc(client, "workflow_start", mutation(project_id=project["id"], **options)))["run_id"]
    if legacy:
        # Existing suites exercise resumption of stored 2.0 receipts. New batch
        # behavior has its own transport/end-to-end tests using legacy=False.
        from app.models import MCPWorkflowRun
        with SessionLocal() as db:
            run = db.get(MCPWorkflowRun, identifier)
            run.workflow_version = "2.0.0"
            run.options = {key: value for key, value in run.options.items() if key not in {"execution_mode", "batch_phase"}}
            db.commit()
    return identifier


def evidence(client, image, phase="final"):
    version = state(client, image)["version"]
    return [ok(rpc(client, "page_preview", {"image_id": image["id"], "kind": kind,
        "frame": "source" if phase == "preflight" else "translation", "expected_version": version}))["preview_id"]
        for kind in (["original", "annotated"] if phase == "preflight" else ["original", "clean", "rendered", "annotated"])]


def review(client, run, image, *, phase="final", outcome="issues", evidence_ids=None, **extra):
    checks = PREFLIGHT_CHECKS if phase == "preflight" else CHECKS
    if phase == "preflight":
        from app.models import MCPWorkflowRun
        from app.services.mcp_batch_workflow import enabled
        with SessionLocal() as db:
            if enabled(db.get(MCPWorkflowRun, run)):
                checks = (*checks, "translation")
    if phase == "preflight":
        extra.setdefault("selection", {"mode": "regions", "region_ids": extra.pop("region_ids", [])})
    elif outcome == "issues":
        extra.setdefault("disposition", "deliver_with_issues")
    return rpc(client, "workflow_review_page", mutation(run_id=run, image_id=image["id"], phase=phase,
        outcome=outcome, checks={name: {"status": "pass" if outcome == "passed" else "uncertain", "reason": "Checked against image"} for name in checks},
        note="Observed test review" if outcome != "unverified" else "Client cannot view images; visual review not performed",
        evidence_ids=evidence_ids if evidence_ids is not None else evidence(client, image, phase),
        expected_versions=state(client, image)["expected_versions"], **extra))


def render_empty(image):
    with SessionLocal() as db:
        row = db.get(ImagePage, image["id"])
        row.rendered_path = row.original_path
        row.metadata_json = {**row.metadata_json, "text_layer_region_ids": []}
        db.commit()


def region(client, image, **values):
    return client.post(f"/api/images/{image['id']}/regions", json={"bbox": [30, 40, 90, 100],
        "source_text": "こんにちは", "translated_text": "Hello", **values}).json()






@pytest.mark.parametrize("failure", ["missing", "stale", "wrong_page", "wrong_frame", "passed_check", "incomplete"])
def test_review_evidence_and_check_guards(client, failure):
    project, image = page(client)
    render_empty(image)
    run = begin(client, project)
    phase = "preflight" if failure == "wrong_frame" else "final"
    ids = evidence(client, image, phase)
    outcome = "issues"
    if failure == "missing":
        ids = []
    elif failure == "stale":
        region(client, image)
    elif failure == "wrong_page":
        _, other = page(client)
        render_empty(other)
        ids = evidence(client, other)
    elif failure == "wrong_frame":
        ids[-1] = ok(rpc(client, "page_preview", {"image_id": image["id"], "kind": "annotated", "frame": "translation"}))["preview_id"]
    elif failure == "passed_check":
        outcome = "passed"
    response = review(client, run, image, phase=phase, evidence_ids=ids, outcome=outcome)
    if failure in {"passed_check", "incomplete"}:
        # Inspect an explicit contradictory/missing check payload rather than a helper's valid one.
        arguments = mutation(run_id=run, image_id=image["id"], phase="final", outcome="passed", note="Check guard",
            evidence_ids=ids, expected_versions=state(client, image)["expected_versions"],
            checks={key: {"status": "pass", "reason": "Seen"} for key in CHECKS})
        if failure == "passed_check":
            arguments["checks"]["ocr"]["status"] = "skipped"
        else:
            del arguments["checks"]["ocr"]
        response = rpc(client, "workflow_review_page", arguments)
    assert response["isError"], response


def test_round_budget_idempotency_and_external_edit_staleness(client):
    project, image = page(client)
    render_empty(image)
    run = begin(client, project)
    ok(review(client, run, image))
    for number in (1, 2):
        arguments = mutation(run_id=run, image_id=image["id"], expected_versions=state(client, image)["expected_versions"])
        first = rpc(client, "workflow_begin_correction", arguments)
        assert ok(first)["round"] == number
        assert rpc(client, "workflow_begin_correction", arguments) == first
        assert rpc(client, "workflow_begin_correction", mutation(run_id=run, image_id=image["id"], expected_versions=state(client, image)["expected_versions"]))["isError"]
        ok(review(client, run, image))
    assert rpc(client, "workflow_begin_correction", mutation(run_id=run, image_id=image["id"], expected_versions=state(client, image)["expected_versions"]))["isError"]
    region(client, image)
    status = ok(rpc(client, "workflow_status", {"run_id": run}))["pages"][0]
    assert status["status"] == "stale" and status["correction_rounds"] == 2
    assert rpc(client, "workflow_export", mutation(run_id=run))["isError"]






def test_unverified_export_reports_and_clean_cbz(client):
    project, image = page(client)
    run = begin(client, project)
    ok(review(client, run, image, outcome="unverified", evidence_ids=[]))
    exported = ok(rpc(client, "workflow_export", mutation(run_id=run)))
    job = ok(rpc(client, "task_wait", {"task_id": exported["job"]["id"], "kind": "file", "seconds": 20}))
    assert job["status"] == "COMPLETED", job
    response = client.get(job["download_path"])
    with ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["0001.png"]
    report = client.get(exported["report_downloads"]["json"])
    assert report.status_code == 200
    value = report.json()
    assert not value["fully_verified"] and value["pages"][0]["review"]["outcome"] == "unverified"
    assert value["pages"][0]["export_rerendered"]
    assert client.get(exported["report_downloads"]["markdown"]).status_code == 200
    assert client.get(exported["report_downloads"]["json"], headers={"Host": "evil.invalid"}).status_code == 403


def test_export_checks_versions_at_worker_snapshot_not_just_submission(client, monkeypatch):
    from app.services.file_jobs import file_worker
    project, image = page(client)
    run = begin(client, project)
    ok(review(client, run, image, outcome="unverified", evidence_ids=[]))
    original = file_worker.export_file
    def altered(identifier, project_id, kind):
        with SessionLocal() as db:
            row = db.get(ImagePage, image["id"])
            row.metadata_json = {**row.metadata_json, "external_change": True}
            db.commit()
        return original(identifier, project_id, kind)
    monkeypatch.setattr(file_worker, "export_file", altered)
    exported = ok(rpc(client, "workflow_export", mutation(run_id=run)))
    job = ok(rpc(client, "task_wait", {"task_id": exported["job"]["id"], "kind": "file", "seconds": 20}))
    assert job["status"] == "FAILED" and "stale" in job["message"]


@pytest.mark.parametrize("kind", ["cbz", "translated"])
def test_workflow_export_existing_formats_preserve_review_receipts(client, kind):
    project, image = page(client)
    render_empty(image)
    run = begin(client, project)
    ok(review(client, run, image, outcome="passed"))
    exported = ok(rpc(client, "workflow_export", mutation(run_id=run, kind=kind)))
    job = ok(rpc(client, "task_wait", {"task_id": exported["job"]["id"], "kind": "file", "seconds": 20}))
    assert job["status"] == "COMPLETED", job
    assert client.get(job["download_path"]).status_code == 200
    assert client.get(exported["report_downloads"]["json"]).json()["fully_verified"]
    resumed = ok(rpc(client, "workflow_status", {"run_id": run}))
    assert resumed["exports"][0]["id"] == job["id"]


def test_quality_hints_use_translated_geometry_and_never_write(client):
    project, image = page(client)
    region(client, image, translated_bbox=[20, 20, 140, 100], rotation=20)
    region(client, image, bbox=[170, 30, 80, 80], translated_bbox=[40, 20, 120, 100])
    run = begin(client, project)
    before = state(client, image)["version"]
    status = ok(rpc(client, "workflow_status", {"run_id": run, "include_details": True}))
    assert "translation_overlap" in {item["code"] for item in status["pages"][0]["quality_hints"]}
    assert state(client, image)["version"] == before






def test_readable_evidence_tile_coverage():
    assert mcp_workflows._covered_area([[0, 0, 100, 100], [50, 0, 100, 100]]) == 15000
    assert mcp_workflows._covered_area([[0, 0, 100, 100], [0, 50, 100, 100]]) == 15000
    assert mcp_workflows._covered_area([]) == 0




def test_long_page_requires_readable_full_coverage_tiles(client):
    project, _ = page(client)
    stream = io.BytesIO()
    Image.new("RGB", (300, 5000), "white").save(stream, "PNG")
    image = client.post(f"/api/projects/{project['id']}/images", files={"files": ("long.png", stream.getvalue())}).json()[0]
    run = begin(client, project, image_ids=[image["id"]])
    ids = evidence(client, image, "preflight")
    assert review(client, run, image, phase="preflight", outcome="passed", evidence_ids=ids)["isError"]
    for top in (0, 2500):
        ids.append(ok(rpc(client, "page_preview", {"image_id": image["id"], "kind": "original", "crop": [0, top, 300, 2500]}))["preview_id"])
    ok(review(client, run, image, phase="preflight", outcome="passed", evidence_ids=ids))


def test_two_agents_cannot_open_multiple_correction_rounds(client):
    from concurrent.futures import ThreadPoolExecutor
    project, image = page(client)
    render_empty(image)
    run = begin(client, project)
    ok(review(client, run, image))
    versions = state(client, image)["expected_versions"]
    def submit(_):
        return rpc(client, "workflow_begin_correction", mutation(run_id=run, image_id=image["id"], expected_versions=versions))
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(submit, range(2)))
    assert sum(not result.get("isError") for result in results) == 1
    assert ok(rpc(client, "workflow_status", {"run_id": run}))["pages"][0]["correction_rounds"] == 1

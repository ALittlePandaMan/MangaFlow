"""Directory ingestion uses real MCP, file queue, validation and editor history."""
from datetime import timedelta
from uuid import uuid4

import pytest
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import FileJob, MCPAsset, Project
from app.models.entities import utcnow
from app.services.mcp_files import asset_path, collect_assets
from sqlalchemy import select
from test_file_jobs import png, wait_job
from test_mcp import mutation, rpc


@pytest.fixture
def client(client):
    client.headers["Host"] = "localhost"
    return client


def ok(result):
    assert not result.get("isError"), result
    return result["structuredContent"]


def asset(client, name="page.png", data=None):
    response = client.post("/mangaflow/files", params={"filename": name}, content=png() if data is None else data)
    assert response.status_code == 200, response.text
    return response.json()["asset_id"]


def test_directory_create_order_resume_and_undo(client):
    ids = [asset(client) for _ in range(3)]
    args = mutation(project_name="漫画 インティメイト", target_language="en", images=[
        {"asset_id": ids[0], "relative_path": "第1章/10.png"},
        {"asset_id": ids[1], "relative_path": "第1章/2.png"},
        {"asset_id": ids[2], "relative_path": "第2章/1.png"}])
    submitted = ok(rpc(client, "project_import", args))
    job = wait_job(client, submitted["job"]["id"])
    assert job["status"] == "COMPLETED", job
    project_id = submitted["project_id"]
    pages = client.get(f"/api/projects/{project_id}/images").json()
    assert [page["filename"] for page in pages] == ["2.png", "10.png", "1.png"]
    assert client.get(f"/api/projects/{project_id}").json()["target_language"] == "en"
    assert ok(rpc(client, "project_import", args))["project_id"] == project_id
    recovered = ok(rpc(client, "project_import_status", {"operation_id": args["operation_id"]}))
    assert recovered["found"] and recovered["job"]["result"]["page_count"] == 3
    assert recovered["operation_id"] == args["operation_id"]
    conflicting = rpc(client, "project_import", {**args, "project_name": "different"})
    assert conflicting["isError"]
    # One history operation removes/restores the complete imported page list.
    history = client.get(f"/api/projects/{project_id}/history").json()
    assert client.post(f"/api/projects/{project_id}/history/undo", json={"expected_entry_id": history["undo_id"]}).status_code == 200
    assert client.get(f"/api/projects/{project_id}/images").json() == []
    history = client.get(f"/api/projects/{project_id}/history").json()
    assert client.post(f"/api/projects/{project_id}/history/redo", json={"expected_entry_id": history["redo_id"]}).status_code == 200
    assert len(client.get(f"/api/projects/{project_id}/images").json()) == 3


@pytest.mark.parametrize("name", ["../escape.png", "/absolute.png", "C:/file.png", ".hidden/file.png", "__MACOSX/file.png", "not.pdf", "a\\b.png"])
def test_directory_rejects_invalid_manifest_before_creating_project(client, name):
    before = len(client.get("/api/projects").json())
    response = rpc(client, "project_import", mutation(project_name="bad", images=[
        {"asset_id": asset(client), "relative_path": name}]))
    assert response["isError"]
    assert len(client.get("/api/projects").json()) == before


def test_corrupt_directory_does_not_publish_any_pages(client):
    ids = [asset(client), asset(client, data=b"corrupt image")]
    submitted = ok(rpc(client, "project_import", mutation(project_name="Corrupt", images=[
        {"asset_id": identifier, "relative_path": f"{index}.png"} for index, identifier in enumerate(ids)])))
    job = wait_job(client, submitted["job"]["id"])
    assert job["status"] == "FAILED"
    assert client.get(f"/api/projects/{submitted['project_id']}/images").json() == []
    assert job["result"]["import_operation_id"]


def test_existing_project_append_and_byte_limit(client, monkeypatch):
    project = client.post("/api/projects", json={"name": "Keep", "target_language": "ja"}).json()
    identifier = asset(client)
    response = rpc(client, "project_import", mutation(project_id=project["id"], target_language="en",
        images=[{"asset_id": identifier, "relative_path": "1.png"}]))
    assert response["isError"]
    args = mutation(project_id=project["id"], images=[{"asset_id": identifier, "relative_path": "1.png"}])
    submitted = ok(rpc(client, "project_import", args))
    assert wait_job(client, submitted["job"]["id"])["status"] == "COMPLETED"
    assert client.get(f"/api/projects/{project['id']}").json()["target_language"] == "ja"
    monkeypatch.setattr(get_settings(), "max_upload_mb", 0)
    response = rpc(client, "project_import", mutation(project_name="Too big", images=[
        {"asset_id": identifier, "relative_path": "2.png"}]))
    assert response["isError"] and response["structuredContent"]["error"]["status"] == 413


def test_active_import_pins_expired_uploads_and_recovers_status(client):
    identifier = asset(client)
    operation_id = str(uuid4())
    with SessionLocal() as db:
        project = Project(name="Pinned")
        db.add(project)
        db.flush()
        # RUNNING avoids the worker claiming this deterministic fixture.
        job = FileJob(project_id=project.id, kind="import", status="RUNNING", filename="Pinned",
            snapshot={"image_assets": [{"asset_id": identifier, "relative_path": "1.png"}]},
            result={"import_operation_id": operation_id})
        db.add(job)
        db.get(MCPAsset, identifier).created_at = utcnow()-timedelta(hours=25)
        db.commit()
        job_id = job.id
    collect_assets()
    assert asset_path(identifier).exists()
    status = ok(rpc(client, "project_import_status", {"operation_id": operation_id, "asset_ids": [identifier]}))
    assert status["found"] and status["available_asset_ids"] == []
    assert status["job"]["id"] == job_id
    with SessionLocal() as db:
        job = db.get(FileJob, job_id)
        job.status = "CANCELLED"
        db.commit()
    collect_assets()
    assert not asset_path(identifier).exists()
    with SessionLocal() as db:
        assert not db.scalar(select(MCPAsset).where(MCPAsset.id == identifier))


def test_directory_connector_download_includes_runtime_module(client):
    from io import BytesIO
    from zipfile import ZipFile
    response = client.get("/api/mcp-settings/connector")
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        assert "mangaflow-mcp/directory-import.mjs" in archive.namelist()


def test_directory_import_rebases_after_user_upload_without_losing_history(client, monkeypatch):
    from app.services import file_jobs
    original = file_jobs.prepare_project_edit
    raced = []

    def concurrent_upload(db, project_id, page_ids):
        prepared = original(db, project_id, page_ids)
        if not raced:
            raced.append(True)
            response = client.post(f"/api/projects/{project_id}/images", files={"files": ("manual.png", png(), "image/png")})
            assert response.status_code == 201
        return prepared

    monkeypatch.setattr(file_jobs, "prepare_project_edit", concurrent_upload)
    submitted = ok(rpc(client, "project_import", mutation(project_name="Concurrent", images=[
        {"asset_id": asset(client), "relative_path": "1.png"}])))
    assert wait_job(client, submitted["job"]["id"])["status"] == "COMPLETED"
    project_id = submitted["project_id"]
    assert [item["filename"] for item in client.get(f"/api/projects/{project_id}/images").json()] == ["manual.png", "1.png"]
    history = client.get(f"/api/projects/{project_id}/history").json()
    assert client.post(f"/api/projects/{project_id}/history/undo", json={"expected_entry_id": history["undo_id"]}).status_code == 200
    assert [item["filename"] for item in client.get(f"/api/projects/{project_id}/images").json()] == ["manual.png"]


def test_restart_keeps_queued_manifest_and_marks_interrupted_import_failed(client, monkeypatch):
    from app.services.file_jobs import FileJobWorker, file_worker
    from app.services.mcp_directory_import import prepared_images
    file_worker.shutdown()
    try:
        identifier = asset(client)
        args = mutation(project_name="Restart", images=[{"asset_id": identifier, "relative_path": "1.png"}])
        submitted = ok(rpc(client, "project_import", args))
        with SessionLocal() as db:
            project_id = submitted["project_id"]
            interrupted = FileJob(project_id=project_id, kind="import", status="RUNNING", filename="Restart",
                snapshot={"image_assets": args["images"]}, result={"import_operation_id": "interrupted-import"})
            db.add(interrupted)
            db.get(MCPAsset, identifier).created_at = utcnow()-timedelta(hours=25)
            db.commit()
            interrupted_id = interrupted.id
        collect_assets()
        worker = FileJobWorker()
        monkeypatch.setattr(worker, "run", lambda: None)
        worker.start()
        worker.shutdown()
        with SessionLocal() as db:
            assert db.get(FileJob, interrupted_id).status == "FAILED"
            queued = db.get(FileJob, submitted["job"]["id"])
            assert queued.status == "QUEUED"
            assert prepared_images(queued.snapshot["image_assets"], lambda: None, lambda value: None)
        assert client.get(f"/api/projects/{project_id}/images").json() == []
        assert ok(rpc(client, "project_import_status", {"operation_id": args["operation_id"]}))["job"]["id"] == submitted["job"]["id"]
    finally:
        file_worker.start()


def test_cancel_during_directory_staging_publishes_no_pages(client, monkeypatch):
    from app.services import file_jobs
    original = file_jobs.get_storage().save_upload
    staged = []

    def cancel_after_save(project_id, page_id, *args, **kwargs):
        result = original(project_id, page_id, *args, **kwargs)
        if not staged:
            staged.append(page_id)
            with SessionLocal() as db:
                job = db.scalar(select(FileJob).where(FileJob.project_id == project_id, FileJob.status == "RUNNING").with_for_update())
                job.status = "CANCELLING"
                db.commit()
        return result

    monkeypatch.setattr(file_jobs.get_storage(), "save_upload", cancel_after_save)
    submitted = ok(rpc(client, "project_import", mutation(project_name="Cancel", images=[
        {"asset_id": asset(client), "relative_path": "1.png"}])))
    assert wait_job(client, submitted["job"]["id"])["status"] == "CANCELLED"
    assert client.get(f"/api/projects/{submitted['project_id']}/images").json() == []
    from app.storage import get_storage
    assert not (get_storage().project_dir(submitted["project_id"]) / "pages" / staged[0]).exists()


def test_recovery_binds_source_across_clients_and_after_file_job_expiry(client):
    args = mutation(project_name="Bound source", source_fingerprint="a"*64,
        images=[{"asset_id": asset(client), "relative_path": "1.png"}])
    submitted = ok(rpc(client, "project_import", args))
    assert wait_job(client, submitted["job"]["id"])["status"] == "COMPLETED"
    for fingerprint, valid in [("a"*64, True), ("b"*64, False)]:
        result = rpc(client, "project_import_status", {"operation_id": args["operation_id"], "source_fingerprint": fingerprint})
        assert bool(result.get("isError")) != valid
    # The durable operation receipt remains bound after the file-job retention window.
    with SessionLocal() as db:
        db.delete(db.get(FileJob, submitted["job"]["id"]))
        db.commit()
    result = ok(rpc(client, "project_import_status", {"operation_id": args["operation_id"], "source_fingerprint": "a"*64}))
    assert result["operation"]["result"]["structuredContent"]["source_fingerprint"] == "a"*64
    assert rpc(client, "project_import_status", {"operation_id": args["operation_id"], "source_fingerprint": "b"*64})["isError"]


def test_orphan_cleanup_respects_locked_registered_asset(client, monkeypatch):
    import os

    from app.services import mcp_files
    identifier = asset(client)
    now = utcnow()
    created = now-timedelta(hours=24)+timedelta(seconds=1)
    path = asset_path(identifier)
    os.utime(path, (created.timestamp(), created.timestamp()))
    with SessionLocal() as db:
        db.get(MCPAsset, identifier).created_at = created
        db.commit()
    with SessionLocal() as admission:
        row = admission.scalar(select(MCPAsset).where(MCPAsset.id == identifier).with_for_update())
        assert row.created_at >= now-timedelta(hours=24)
        monkeypatch.setattr(mcp_files, "utcnow", lambda: now+timedelta(seconds=2))
        collect_assets()
        assert path.exists()  # The row was skipped, so its file must also be retained.
    collect_assets()
    assert not path.exists()  # Normal expiration works once the admission lock is gone.


def test_mcp_file_jobs_keep_their_source_through_completion(client):
    from test_mcp import page
    from test_mcp_workflows import begin, review
    project, image = page(client)
    web = client.post(f"/api/projects/{project['id']}/exports", json={"kind": "json"}).json()
    run = begin(client, project)
    ok(review(client, run, image, outcome="unverified", evidence_ids=[]))
    mcp = ok(rpc(client, "workflow_export", mutation(run_id=run)))["job"]
    assert web["source"] == "web"
    for job_id, source in ((web["id"], "web"), (mcp["id"], "mcp")):
        wait_job(client, job_id)
        assert client.get(f"/api/file-jobs/{job_id}").json()["source"] == source

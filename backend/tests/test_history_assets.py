import json
from io import BytesIO

import pytest
from app.core.database import SessionLocal, engine
from app.core.transactions import is_retryable_write
from app.models import (
    EditorHistoryEntry,
    ProcessingTask,
    Project,
    RegionRevision,
    StageCheckpoint,
    TaskWorkflow,
    TextRegion,
)
from app.services import history_assets
from app.services.history_assets import collect_project_history_assets
from app.services.regions import region_snapshot
from app.storage import get_storage
from db_assertions import assert_rows_unlocked
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from test_editor_history import apply
from test_repair_geometry_sync import setup_page


def test_region_and_mask_history_only_capture_affected_regions(client):
    page, regions = setup_page(client, 35)
    assert client.patch(f"/api/regions/{regions[8]['id']}", json={"font_size": 39}).status_code == 200
    with SessionLocal() as db:
        entry = db.scalar(
            select(EditorHistoryEntry)
            .where(EditorHistoryEntry.project_id == page["project_id"])
            .order_by(EditorHistoryEntry.id.desc())
        )
        assert entry.before["_partial_region_ids"] == [regions[8]["id"]]
        assert len(entry.before["regions"]) == len(entry.after["regions"]) == 1
        # Compare against live region data; do not keep an obsolete full-page
        # archive path solely to measure the partial snapshot's size.
        full_regions = [region_snapshot(db.get(TextRegion, row['id'])) for row in regions]
        assert len(json.dumps(entry.before)) < len(json.dumps(full_regions)) / 5
    restored = apply(client, page["project_id"])["regions"]
    assert len(restored) == 35
    assert all(
        row["font_size"] == next(original["font_size"] for original in regions if original["id"] == row["id"])
        for row in restored
    )
    apply(client, page["project_id"], "redo")
    data = BytesIO()
    Image.new("L", (200, 200), 0).save(data, "PNG")
    assert (
        client.put(
            f"/api/regions/{regions[8]['id']}/mask", files={"file": ("mask.png", data.getvalue(), "image/png")}
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        entry = db.scalar(
            select(EditorHistoryEntry)
            .where(EditorHistoryEntry.project_id == page["project_id"])
            .order_by(EditorHistoryEntry.id.desc())
        )
        assert len(entry.before["regions"]) == 1
    assert len(apply(client, page["project_id"])["regions"]) == 35


def test_history_collection_preserves_live_undo_revision_and_young_assets(client):
    page, regions = setup_page(client)
    storage = get_storage()
    directory = storage.project_dir(page["project_id"]) / "history" / "assets"
    directory.mkdir(parents=True, exist_ok=True)
    live = directory / "live.png"
    revision = directory / "revision.png"
    orphan = directory / "orphan.png"
    for path in (live, revision, orphan):
        path.write_bytes(b"asset")
    with SessionLocal() as db:
        region = db.get(TextRegion, regions[0]["id"])
        region.pixel_mask_path = storage.relative(live)
        db.add(
            RegionRevision(region_id=region.id, action="test", snapshot={"pixel_mask_path": storage.relative(revision)})
        )
        db.commit()
        assert collect_project_history_assets(db, page["project_id"])["removed_files"] == 0
        result = collect_project_history_assets(db, page["project_id"], grace_seconds=0)
    assert result == {"removed_files": 1, "removed_bytes": 5}
    assert live.is_file() and revision.is_file() and not orphan.exists()
    restored = apply(client, page["project_id"])
    assert restored["regions"] == []  # Existing full-state creation history still works.
    assert client.get(restored["page"]["original_url"]).status_code == 200


def test_collection_never_runs_through_active_tasks_and_is_bounded(client):
    page, _ = setup_page(client)
    directory = get_storage().project_dir(page["project_id"]) / "history" / "assets"
    for index in range(3):
        (directory / f"orphan-{index}.png").write_bytes(b"asset")
    with SessionLocal() as db:
        task = ProcessingTask(project_id=page["project_id"], image_id=page["id"], task_type="ocr", status="PAUSED")
        db.add(task)
        db.commit()
        with pytest.raises(HTTPException):
            collect_project_history_assets(db, page["project_id"], grace_seconds=0)
        db.rollback()
        task.status = "CANCELLED"
        db.commit()
        assert (
            collect_project_history_assets(db, page["project_id"], grace_seconds=0, max_files=1)["removed_files"] == 1
        )
    assert len(list(directory.glob("orphan-*.png"))) == 2


def test_history_collection_preserves_failed_workflow_checkpoint_inputs(client):
    page, _ = setup_page(client)
    storage = get_storage()
    directory = storage.project_dir(page["project_id"]) / "history" / "assets"
    saved_input, saved_output, orphan = [directory / name for name in ("input.png", "result.png", "unused.png")]
    for path in (saved_input, saved_output, orphan):
        path.write_bytes(b"asset")
    with SessionLocal() as db:
        task = ProcessingTask(project_id=page["project_id"], image_id=page["id"], task_type="full", status="FAILED")
        db.add(task)
        db.flush()
        run = TaskWorkflow(task_id=task.id, generation=1, input_fingerprint="0"*64, request_fingerprint="1"*64,
            input_state={"mask": storage.relative(saved_input)}, policy={})
        db.add(run)
        db.flush()
        db.add(StageCheckpoint(workflow_id=run.id, stage="mask", attempt=1, generation=1, input_fingerprint="2"*64,
            status="SUCCEEDED", result={"mask": storage.relative(saved_output)}))
        db.commit()
        assert collect_project_history_assets(db, page["project_id"], grace_seconds=0)["removed_files"] == 1
    assert saved_input.exists() and saved_output.exists() and not orphan.exists()


def test_history_reference_scan_does_not_hold_project_or_page_row_locks(client, monkeypatch):
    page, _ = setup_page(client)
    directory = get_storage().project_dir(page["project_id"]) / "history" / "assets"
    orphan = directory / "unused.png"
    orphan.write_bytes(b"asset")
    original = history_assets._history_references
    checked = []

    def references(db, project_id, prefix):
        assert_rows_unlocked(engine, project_id, page["id"])
        checked.append(True)
        return original(db, project_id, prefix)

    monkeypatch.setattr(history_assets, "_history_references", references)
    with SessionLocal() as db:
        assert collect_project_history_assets(db, page["project_id"], grace_seconds=0)["removed_files"] == 1
        assert not db.in_transaction()
    assert checked == [True] and not orphan.exists()


def test_history_collection_revalidates_project_after_unlocked_scan(client, monkeypatch):
    page, regions = setup_page(client)
    storage = get_storage()
    orphan = storage.project_dir(page["project_id"]) / "history" / "assets" / "newly-used.png"
    orphan.write_bytes(b"asset")
    original = history_assets._history_references

    def references(db, project_id, prefix):
        result = original(db, project_id, prefix)
        with SessionLocal() as writer:
            # Same no-op project update as every application publisher.
            writer.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
            writer.get(TextRegion, regions[0]["id"]).pixel_mask_path = storage.relative(orphan)
            writer.commit()
        return result

    monkeypatch.setattr(history_assets, "_history_references", references)
    with SessionLocal() as db:
        assert collect_project_history_assets(db, page["project_id"], grace_seconds=0)["removed_files"] == 0
    assert orphan.is_file()


def test_history_collection_does_not_wait_on_a_busy_project(client):
    page, _ = setup_page(client)
    directory = get_storage().project_dir(page["project_id"]) / "history" / "assets"
    orphan = directory / "unused.png"
    orphan.write_bytes(b"asset")
    with engine.connect() as writer, SessionLocal() as db:
        writer.execute(update(Project).where(Project.id == page["project_id"]).values(updated_at=Project.updated_at))
        with pytest.raises(OperationalError) as error:
            collect_project_history_assets(db, page["project_id"], grace_seconds=0)
        assert is_retryable_write(error.value)
        assert not db.in_transaction()
    assert orphan.is_file()


def test_history_collection_rechecks_tasks_created_during_reference_scan(client, monkeypatch):
    page, _ = setup_page(client)
    orphan = get_storage().project_dir(page["project_id"]) / "history" / "assets" / "unused.png"
    orphan.write_bytes(b"asset")
    original = history_assets._history_references

    def references(db, project_id, prefix):
        result = original(db, project_id, prefix)
        with SessionLocal() as writer:
            # A newly queued task does not update the project's xmin.
            writer.add(ProcessingTask(project_id=project_id, image_id=page["id"], task_type="ocr", status="PAUSED"))
            writer.commit()
        return result

    monkeypatch.setattr(history_assets, "_history_references", references)
    with SessionLocal() as db:
        with pytest.raises(HTTPException):
            collect_project_history_assets(db, page["project_id"], grace_seconds=0)
        assert not db.in_transaction()
    assert orphan.is_file()

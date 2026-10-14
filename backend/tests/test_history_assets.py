import json
from io import BytesIO

import pytest
from app.core.database import SessionLocal
from app.models import EditorHistoryEntry, ImagePage, ProcessingTask, RegionRevision, TextRegion
from app.services.editor_history import capture_page_state
from app.services.history_assets import collect_project_history_assets
from app.storage import get_storage
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select
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
        full = capture_page_state(db, db.get(ImagePage, page['id']))
        assert len(json.dumps(entry.before)) < len(json.dumps(full)) / 5
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

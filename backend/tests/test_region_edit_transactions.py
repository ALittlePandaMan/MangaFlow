from __future__ import annotations

import sqlite3
from io import BytesIO

import pytest
from app.core.database import SessionLocal, engine
from app.models import EditorHistoryEntry, ImagePage, Project, RegionRevision, TextRegion
from app.services import region_edits
from PIL import Image
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from test_editor_history import apply as undo
from test_repair_geometry_sync import setup_page


def _check_writer_available(project_id):
    with engine.connect() as connection:
        if connection.dialect.name == "sqlite":
            previous = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
            connection.exec_driver_sql("PRAGMA busy_timeout=50")
            try:
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            finally:
                connection.rollback()
                connection.exec_driver_sql(f"PRAGMA busy_timeout={int(previous)}")
        else:
            connection.execute(select(Project.id).where(Project.id == project_id).with_for_update(nowait=True))
            list(connection.scalars(select(ImagePage.id).where(ImagePage.project_id == project_id).with_for_update(nowait=True)))
            connection.rollback()


def test_reorder_uses_the_order_from_its_prepared_snapshot(client, monkeypatch):
    from app.api import images
    from app.services.page_edits import apply_page_order
    from test_repair_geometry_sync import png

    identifier = client.post("/api/projects", json={"name": "concurrent reorder"}).json()["id"]
    pages = client.post(f"/api/projects/{identifier}/images", files=[
        ("files", (f"{index}.png", png(), "image/png")) for index in range(3)]).json()
    first, second, third = [page["id"] for page in pages]
    original = images.prepare_project_edit

    def concurrent(db, project_id, page_ids):
        with SessionLocal() as other:
            apply_page_order(other, project_id, [
                {"id": second, "order_index": 0}, {"id": first, "order_index": 1},
                {"id": third, "order_index": 2}])
            other.commit()
        return original(db, project_id, page_ids)

    monkeypatch.setattr(images, "prepare_project_edit", concurrent)
    response = client.patch(f"/api/images/{first}/order?order_index=2")
    assert response.status_code == 200, response.text
    current = client.get(f"/api/projects/{identifier}/images").json()
    assert [(page["id"], page["order_index"]) for page in current] == [(second, 0), (third, 1), (first, 2)]
    undo(client, identifier)
    current = client.get(f"/api/projects/{identifier}/images").json()
    assert [(page["id"], page["order_index"]) for page in current] == [(second, 0), (first, 1), (third, 2)]


def test_page_import_delete_reset_and_status_prepare_files_without_writer(client, monkeypatch):
    from app.services import page_edits
    from app.storage import get_storage
    from test_repair_geometry_sync import png

    page, regions = setup_page(client)
    identifier = page["project_id"]
    archive, region_archive, upload = page_edits._archive_paths, region_edits._archive_paths, get_storage().save_upload
    calls = []

    def unlocked(function, label, *args, **kwargs):
        _check_writer_available(identifier)
        calls.append(label)
        return function(*args, **kwargs)

    monkeypatch.setattr(page_edits, "_archive_paths", lambda *args, **kwargs: unlocked(archive, "project_archive", *args, **kwargs))
    monkeypatch.setattr(region_edits, "_archive_paths", lambda *args, **kwargs: unlocked(region_archive, "page_archive", *args, **kwargs))
    monkeypatch.setattr(get_storage(), "save_upload", lambda *args, **kwargs: unlocked(upload, "upload", *args, **kwargs))
    added = client.post(f"/api/projects/{identifier}/images", files={"files": ("new.png", png(), "image/png")})
    assert added.status_code == 201, added.text
    assert client.delete(f"/api/images/{added.json()[0]['id']}").status_code == 204
    assert undo(client, identifier)["page"]["id"] == added.json()[0]["id"]
    assert client.post(f"/api/images/{page['id']}/reset").status_code == 200
    assert client.get(f"/api/images/{page['id']}/regions").json() == []
    assert undo(client, identifier)["regions"][0]["id"] == regions[0]["id"]
    response = client.patch(f"/api/images/{page['id']}/ocr-exempt?exempt=true")
    assert response.status_code == 200, response.text
    assert "upload" in calls and calls.count("project_archive") == 4 and calls.count("page_archive") >= 4


def test_concurrent_page_change_rejects_prepared_deletion(client, monkeypatch):
    from app.services import page_edits

    page, regions = setup_page(client)
    archive, changed = page_edits._archive_paths, []

    def concurrent(*args, **kwargs):
        result = archive(*args, **kwargs)
        if not changed:
            changed.append(True)
            with SessionLocal() as other:
                other.get(TextRegion, regions[0]["id"]).source_text = "keep newer user input"
                other.commit()
        return result

    monkeypatch.setattr(page_edits, "_archive_paths", concurrent)
    response = client.delete(f"/api/images/{page['id']}")
    assert response.status_code == 409, response.text
    assert client.get(f"/api/images/{page['id']}/regions").json()[0]["source_text"] == "keep newer user input"


def test_import_write_retry_does_not_repeat_upload_or_create_duplicate_history(client, monkeypatch):
    from app.storage import get_storage
    from test_repair_geometry_sync import png

    if engine.dialect.name != "sqlite":
        pytest.skip("SQLite fault injection; PostgreSQL retries are tested separately")
    identifier = client.post("/api/projects", json={"name": "import write conflict"}).json()["id"]
    upload, calls, failures = get_storage().save_upload, [], []

    def record(*args, **kwargs):
        calls.append(1)
        return upload(*args, **kwargs)

    def fail_once(connection, cursor, statement, parameters, context, executemany):
        if not failures and statement.startswith("INSERT INTO editor_history"):
            failures.append(1)
            raise OperationalError("test", {}, sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(get_storage(), "save_upload", record)
    event.listen(engine, "before_cursor_execute", fail_once)
    try:
        response = client.post(f"/api/projects/{identifier}/images", files={"files": ("new.png", png(), "image/png")})
    finally:
        event.remove(engine, "before_cursor_execute", fail_once)
    assert response.status_code == 201, response.text
    assert calls == failures == [1]
    assert len(client.get(f"/api/projects/{identifier}/images").json()) == 1
    assert client.get(f"/api/projects/{identifier}/history").json()["undo_count"] == 1


def test_import_unknown_commit_does_not_remove_committed_originals(client, monkeypatch):
    import app.api.projects as api
    from test_repair_geometry_sync import png

    identifier = client.post("/api/projects", json={"name": "unknown import outcome"}).json()["id"]
    original = api.publish_project_edit

    def lost_acknowledgement(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("Simulated lost COMMIT acknowledgement")

    monkeypatch.setattr(api, "publish_project_edit", lost_acknowledgement)
    with pytest.raises(ConnectionError, match="COMMIT"):
        client.post(f"/api/projects/{identifier}/images", files={"files": ("new.png", png(), "image/png")})
    pages = client.get(f"/api/projects/{identifier}/images").json()
    assert len(pages) == 1
    assert client.get(pages[0]["original_url"]).status_code == 200


def test_property_edit_archives_both_sides_without_holding_a_writer_or_row_lock(client,monkeypatch):
    page,regions = setup_page(client,2)
    original = region_edits._archive_paths
    calls = []

    def unlocked(*args,**kwargs):
        with engine.connect() as connection:
            if connection.dialect.name=="sqlite":
                previous = connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
                connection.exec_driver_sql("PRAGMA busy_timeout=50")
                try:
                    connection.exec_driver_sql("BEGIN IMMEDIATE")
                finally:
                    connection.rollback()
                    connection.exec_driver_sql(f"PRAGMA busy_timeout={int(previous)}")
            else:
                connection.execute(select(Project.id).where(Project.id==page["project_id"]).with_for_update(nowait=True))
                connection.execute(select(ImagePage.id).where(ImagePage.id==page["id"]).with_for_update(nowait=True))
                connection.rollback()
        calls.append(1)
        return original(*args,**kwargs)

    monkeypatch.setattr(region_edits,"_archive_paths",unlocked)
    response = client.patch(f"/api/regions/{regions[0]['id']}",json={"font_size":33,"rotation":12})
    assert response.status_code==200,response.text
    assert len(calls)==3  # Before, one independent revision, after.
    restored = undo(client,page["project_id"])
    assert restored["regions"][0]["font_size"]==regions[0]["font_size"]
    assert restored["regions"][1]["id"]==regions[1]["id"]
    redone = undo(client,page["project_id"],"redo")
    assert redone["regions"][0]["font_size"]==33 and redone["regions"][0]["rotation"]==12


def test_late_property_save_rejects_stale_input_without_overwriting_the_other_edit(client,monkeypatch):
    page,regions = setup_page(client)
    original = region_edits._archive_paths
    changed = []
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(EditorHistoryEntry).where(EditorHistoryEntry.project_id==page["project_id"]))

    def edit_while_archiving(*args,**kwargs):
        value = original(*args,**kwargs)
        if not changed:
            changed.append(1)
            with SessionLocal() as other:
                other.get(TextRegion,regions[0]["id"]).source_text = "concurrent edit"
                other.commit()
        return value

    monkeypatch.setattr(region_edits,"_archive_paths",edit_while_archiving)
    response = client.patch(f"/api/regions/{regions[0]['id']}",json={"font_size":45})
    assert response.status_code==409,response.text
    row = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert row["source_text"]=="concurrent edit" and row["font_size"]==regions[0]["font_size"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(EditorHistoryEntry).where(EditorHistoryEntry.project_id==page["project_id"]))==before


def test_bulk_edit_retries_only_the_short_save_without_rearchiving_or_duplicate_revisions(client,monkeypatch):
    if engine.dialect.name!="sqlite":
        pytest.skip("SQLite contention injection; PostgreSQL row locks are tested independently")
    page,regions = setup_page(client,2)
    original = region_edits._archive_paths
    calls,failures = [],[]
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(RegionRevision).where(RegionRevision.region_id.in_([row["id"] for row in regions])))

    def archive(*args,**kwargs):
        calls.append(1)
        return original(*args,**kwargs)

    def fail_once(connection,cursor,statement,parameters,context,executemany):
        if not failures and statement.startswith("INSERT INTO editor_history"):
            failures.append(1)
            raise OperationalError("test",{},sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(region_edits,"_archive_paths",archive)
    event.listen(engine,"before_cursor_execute",fail_once)
    try:
        result = client.post("/api/regions/bulk-update",json=[{"id":row["id"],"patch":{"font_size":34}} for row in regions])
    finally:
        event.remove(engine,"before_cursor_execute",fail_once)
    assert result.status_code==200,result.text
    assert len(calls)==4 and failures==[1]  # Two revisions; never replay archive work.
    assert all(row["font_size"]==34 for row in result.json())
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RegionRevision).where(
            RegionRevision.region_id.in_([row["id"] for row in regions])))==before+2
    restored = undo(client,page["project_id"])
    assert [row["font_size"] for row in restored["regions"]]==[row["font_size"] for row in regions]


def test_mask_file_and_sparse_history_are_prepared_without_a_writer(client,monkeypatch):
    import app.api.regions as api

    page,regions = setup_page(client)
    original = api.mask_checkpoint
    calls = []

    def no_writer(*args,**kwargs):
        with engine.connect() as connection:
            if connection.dialect.name=="sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.execute(select(Project.id).where(Project.id==page["project_id"]).with_for_update(nowait=True))
                connection.execute(select(ImagePage.id).where(ImagePage.id==page["id"]).with_for_update(nowait=True))
            connection.rollback()
        calls.append(1)
        return original(*args,**kwargs)

    monkeypatch.setattr(api,"mask_checkpoint",no_writer)
    buffer = BytesIO()
    Image.new("L",(3,4),255).save(buffer,"PNG")
    buffer.seek(0)
    result = client.put(f"/api/regions/{regions[0]['id']}/mask",
        files={"file":("patch.png",buffer,"image/png")},
        data={"x":10,"y":20,"expected_updated_at":regions[0]["updated_at"]})
    assert result.status_code==200,result.text
    assert calls==[1]
    window = client.get(f"/api/regions/{regions[0]['id']}/mask-history").json()
    assert window["region_updated_at"]==result.json()["updated_at"] and len(window["undo"])==1
    restored = undo(client,page["project_id"])
    assert restored["regions"][0]["mask_url"] is None
    redone = undo(client,page["project_id"],"redo")
    assert redone["regions"][0]["mask_url"] is not None


def test_merge_retains_independently_restorable_mask_revision_after_live_mask_cleanup(client):
    from app.storage import get_storage

    _,regions = setup_page(client,2)
    for region in regions:
        buffer = BytesIO()
        Image.new("L",(200,200),255).save(buffer,"PNG")
        buffer.seek(0)
        assert client.put(f"/api/regions/{region['id']}/mask",files={"file":("mask.png",buffer,"image/png")}).status_code==200
    merged = client.post("/api/regions/merge",json=[row["id"] for row in regions])
    assert merged.status_code==200,merged.text
    identifier = merged.json()["region"]["id"]
    with SessionLocal() as db:
        revision = db.scalar(select(RegionRevision).where(RegionRevision.region_id==identifier,
            RegionRevision.action=="before_merge").order_by(RegionRevision.created_at.desc()))
        assert revision is not None
        path,revision_id = revision.snapshot["pixel_mask_path"],revision.id
        assert "/history/assets/" in path and get_storage().absolute(path).is_file()
    restored = client.post(f"/api/regions/{identifier}/revisions/{revision_id}/restore")
    assert restored.status_code==200,restored.text
    assert client.get(restored.json()["mask_url"]).status_code==200


def test_undo_prepares_files_without_writer_and_never_overwrites_an_intervening_edit(client,monkeypatch):
    from app.services import editor_history

    page,regions = setup_page(client)
    assert client.patch(f"/api/regions/{regions[0]['id']}",json={"font_size":41}).status_code==200
    state = client.get(f"/api/projects/{page['project_id']}/history").json()
    original = editor_history._prepare_restore_page
    calls = []

    def prepare_without_writer(*args,**kwargs):
        with engine.connect() as connection:
            if connection.dialect.name=="sqlite":
                connection.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                connection.execute(select(Project.id).where(Project.id==page["project_id"]).with_for_update(nowait=True))
            connection.rollback()
        value = original(*args,**kwargs)
        calls.append(1)
        with SessionLocal() as other:
            other.get(TextRegion,regions[0]["id"]).source_text = "changed during undo preparation"
            other.commit()
        return value

    monkeypatch.setattr(editor_history,"_prepare_restore_page",prepare_without_writer)
    result = client.post(f"/api/projects/{page['project_id']}/history/undo",json={"expected_entry_id":state["undo_id"]})
    assert result.status_code==409,result.text
    assert calls==[1]
    current = client.get(f"/api/images/{page['id']}/regions").json()[0]
    assert current["font_size"]==41 and current["source_text"]=="changed during undo preparation"
    assert client.get(f"/api/projects/{page['project_id']}/history").json()["undo_id"]==state["undo_id"]


def test_undo_short_write_retries_do_not_repeat_file_preparation_or_revisions(client,monkeypatch):
    from app.services import editor_history

    if engine.dialect.name!="sqlite":
        pytest.skip("SQLite abort injection; PostgreSQL concurrency is covered separately")
    page,regions = setup_page(client)
    assert client.patch(f"/api/regions/{regions[0]['id']}",json={"font_size":39}).status_code==200
    original = editor_history._prepare_restore_page
    calls,failures = [],[]
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(RegionRevision).where(RegionRevision.region_id==regions[0]["id"]))

    def prepare(*args,**kwargs):
        calls.append(1)
        return original(*args,**kwargs)

    def fail_once(connection,cursor,statement,parameters,context,executemany):
        if not failures and statement.startswith("UPDATE editor_history"):
            failures.append(1)
            raise OperationalError("test",{},sqlite3.OperationalError("database is locked"))

    monkeypatch.setattr(editor_history,"_prepare_restore_page",prepare)
    event.listen(engine,"before_cursor_execute",fail_once)
    try:
        restored = undo(client,page["project_id"])
    finally:
        event.remove(engine,"before_cursor_execute",fail_once)
    assert restored["regions"][0]["font_size"]==regions[0]["font_size"]
    assert calls==failures==[1]
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(RegionRevision).where(RegionRevision.region_id==regions[0]["id"]))==before+1

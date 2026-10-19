from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from app.core.database import Base
from app.core.db_engine import create_database_engine
from app.core.migrations import HEAD, database_revision, migration_config, upgrade_database
from app.models import (
    EditorHistoryEntry,
    ImagePage,
    ModelConfig,
    ProcessingBatch,
    ProcessingTask,
    Project,
    StageCheckpoint,
    TaskWorkflow,
    TextRegion,
)
from app.services.database_transfer import backup_sqlite, copy_database, table_digest, validate_external_references
from cryptography.fernet import Fernet
from sqlalchemy import MetaData, inspect, select, text
from sqlalchemy.orm import Session


@pytest.fixture(params=["sqlite", "postgresql"])
def target(request, tmp_path):
    if request.param == "sqlite":
        engine = create_database_engine(f"sqlite:///{tmp_path / 'target.db'}")
        yield engine
        engine.dispose()
        return
    url = os.environ.get("MANGAFLOW_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Set MANGAFLOW_TEST_POSTGRES_URL for isolated PostgreSQL integration tests")
    # A unique schema prevents tests from touching any existing tables.
    schema = "test_" + uuid4().hex
    admin = create_database_engine(url)
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    engine = create_database_engine(url, connect_args={"options": f"-c timezone=UTC -c search_path={schema}"})
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def seed(engine, root):
    key = Fernet.generate_key()
    with Session(engine) as db:
        project = Project(name="migration 日本語", settings={"nested": [1, "中文", None]})
        db.add(project)
        db.flush()
        path = f"projects/{project.id}/original.png"
        asset = root / path
        asset.parent.mkdir(parents=True)
        asset.write_bytes(b"unchanged image bytes")
        project.cover_path = path
        page = ImagePage(project_id=project.id, filename="original.webp", original_path=path, width=400, height=500)
        db.add(page)
        db.flush()
        region = TextRegion(image_id=page.id, region_key="R001", source_text="こんにちは", translated_text="你好",
                            polygon=[[0, 1], [2, 3]], bbox=[0, 1, 2, 3], font_size=37.125, visible=False,
                            layout_data={"manual": True, "rotation": 22.5})
        db.add(region)
        db.flush()
        db.add_all([ProcessingTask(project_id=project.id, image_id=page.id, task_type="ocr", status=status)
                    for status in ("QUEUED", "PAUSED", "FAILED")])
        db.add(ModelConfig(kind="translation", name="private", provider="mock", config={"model": "test"},
                           encrypted_api_key=Fernet(key).encrypt(b"do-not-print").decode()))
        db.add(EditorHistoryEntry(id=73, project_id=project.id, image_id=page.id, action="snapshot", before={},
                                  after={"page": {"original_path": path}}, created_at=datetime(2026, 10, 17, tzinfo=timezone.utc)))
        db.commit()
        return key, project.id


def test_fresh_and_repeated_schema_upgrade_matches_orm(target):
    upgrade_database(target)
    upgrade_database(target)
    assert database_revision(target) == HEAD
    with target.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []


def test_batch_migration_preserves_existing_task_checkpoints(target):
    with target.begin() as connection:
        config = migration_config()
        config.attributes["connection"] = connection
        command.upgrade(config, "20261018_04")
        previous = MetaData()
        previous.reflect(connection)
        now = datetime.now(timezone.utc)
        connection.execute(previous.tables["processing_tasks"].insert().values(
            id="old-task", task_type="full", status="FAILED", progress=.6, message="old failure",
            payload={}, pause_requested=False, execution_generation=1, created_at=now, updated_at=now,
        ))
        connection.execute(TaskWorkflow.__table__.insert().values(
            id="old-workflow", task_id="old-task", generation=1, input_fingerprint="input",
            request_fingerprint="request", input_state={}, policy={},
        ))
        connection.execute(StageCheckpoint.__table__.insert().values(
            id="old-checkpoint", workflow_id="old-workflow", stage="translation", attempt=1,
            generation=1, input_fingerprint="stage-input", status="SUCCEEDED", cloud_request=True,
            result={"translated_text":"paid result must survive"},
        ))
    upgrade_database(target)
    with Session(target) as db:
        task = db.get(ProcessingTask, "old-task")
        assert task.status == "FAILED" and task.active_stages == [] and task.batch_id is None
        assert db.get(StageCheckpoint, "old-checkpoint").result == {"translated_text":"paid result must survive"}
    with target.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_copy_preserves_every_row_and_autoincrement_and_rejects_nonempty(target, tmp_path):
    source = create_database_engine(f"sqlite:///{tmp_path / 'source.db'}")
    upgrade_database(source)
    key, project_id = seed(source, tmp_path)
    try:
        result = copy_database(source, target, chunk_size=1)
        assert result["verified"] and result["tables"]["editor_history"]["count"] == 1
        assert validate_external_references(target, tmp_path, secret_key=key)["verified"]
        with Session(target) as db:
            assert db.get(Project, project_id).name == "migration 日本語"
            assert db.get(Project, project_id).cover_path == f"projects/{project_id}/original.png"
            assert set(db.scalars(select(ProcessingTask.status))) == {"QUEUED", "PAUSED", "FAILED"}
            row = EditorHistoryEntry(project_id=project_id, action="new", before={}, after={})
            db.add(row)
            db.commit()
            assert row.id > 73
        with pytest.raises(ValueError, match="contains data"):
            copy_database(source, target)
        rollback = create_database_engine(f"sqlite:///{tmp_path / 'rollback.db'}")
        try:
            reverse = copy_database(target, rollback)
            assert reverse["tables"]["editor_history"]["count"] == 2
            assert validate_external_references(rollback, tmp_path, secret_key=key)["verified"]
        finally:
            rollback.dispose()
    finally:
        source.dispose()


def test_unversioned_legacy_backfill_preserves_source_geometry(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    upgrade_database(engine)
    seed(engine, tmp_path)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TABLE alembic_version")
        for column in ("translated_polygon", "translated_bbox", "visible", "perspective_warp"):
            connection.exec_driver_sql(f"ALTER TABLE text_regions DROP COLUMN {column}")
    upgrade_database(engine)
    with Session(engine) as db:
        region = db.scalar(select(TextRegion))
        assert region.translated_bbox == region.bbox and region.translated_polygon == region.polygon
        assert region.visible and not region.perspective_warp
    engine.dispose()


def test_upgrade_preserves_legacy_cover_and_adds_it_when_missing(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'legacy-cover.db'}")
    upgrade_database(engine)
    _, project_id = seed(engine, tmp_path)
    with engine.begin() as connection:
        connection.exec_driver_sql("DELETE FROM alembic_version")
    upgrade_database(engine)
    with Session(engine) as db:
        assert db.get(Project, project_id).cover_path == f"projects/{project_id}/original.png"
    with engine.begin() as connection:
        connection.exec_driver_sql("ALTER TABLE projects DROP COLUMN cover_path")
        connection.exec_driver_sql("UPDATE alembic_version SET version_num='20261018_02'")
    upgrade_database(engine)
    with Session(engine) as db:
        assert db.get(Project, project_id).cover_path is None
    engine.dispose()


def test_snapshot_includes_wal_and_never_overwrites_backup(tmp_path):
    original = tmp_path / "original.db"
    engine = create_database_engine(f"sqlite:///{original}")
    upgrade_database(engine)
    seed(engine, tmp_path)
    backup = tmp_path / "backup.db"
    assert backup_sqlite(original, backup)["integrity"] == "ok"
    with pytest.raises(FileExistsError):
        backup_sqlite(original, backup)
    copied = create_database_engine(f"sqlite:///{backup}")
    try:
        with engine.connect() as a, copied.connect() as b:
            meta = MetaData()
            meta.reflect(a)
            for table in meta.sorted_tables:
                assert table_digest(a, table) == table_digest(b, table)
    finally:
        engine.dispose()
        copied.dispose()


def test_invalid_backup_is_closed_and_removed_without_changing_source(tmp_path):
    import sqlite3
    from contextlib import closing

    original, backup = tmp_path / "invalid.db", tmp_path / "invalid-backup.db"
    with closing(sqlite3.connect(original)) as db:
        db.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY)")
        db.execute("CREATE TABLE child (id INTEGER REFERENCES parent(id))")
        db.execute("INSERT INTO child VALUES (999)")
        db.commit()
    before = original.read_bytes()
    with pytest.raises(ValueError, match="foreign-key violations"):
        backup_sqlite(original, backup)
    assert not backup.exists()
    assert original.read_bytes() == before


def test_failed_transfer_rolls_back_all_target_rows(target, tmp_path, monkeypatch):
    from app.services import database_transfer
    source = create_database_engine(f"sqlite:///{tmp_path / 'source.db'}")
    upgrade_database(source)
    seed(source, tmp_path)
    monkeypatch.setattr(database_transfer, "table_digest", lambda *args: {"count": -1})
    with pytest.raises(ValueError, match="verification failed"):
        copy_database(source, target)
    with target.connect() as connection:
        for name in inspect(connection).get_table_names():
            if name != "alembic_version":
                assert connection.execute(text(f'SELECT * FROM "{name}"')).first() is None
    source.dispose()


def test_invalid_secret_key_and_missing_assets_are_detected(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'source.db'}")
    upgrade_database(engine)
    key, project_id = seed(engine, tmp_path)
    with pytest.raises(ValueError, match="original secret"):
        validate_external_references(engine, tmp_path)
    (tmp_path / "projects" / project_id / "original.png").unlink()
    result = validate_external_references(engine, tmp_path, secret_key=key)
    assert not result["verified"] and len(result["missing_assets"]) == 1
    engine.dispose()


def test_asset_validation_checks_frozen_provider_keys_and_path_keyed_manifests(tmp_path):
    from cryptography.fernet import InvalidToken

    engine = create_database_engine(f"sqlite:///{tmp_path / 'private-batch.db'}")
    upgrade_database(engine)
    key,project_id = seed(engine,tmp_path)
    other_key = Fernet.generate_key()
    with Session(engine) as db:
        batch = ProcessingBatch(project_id=project_id,policy={"models":[{
            "encrypted_api_key":Fernet(other_key).encrypt(b"private frozen provider").decode(),
        }]},contexts={})
        db.add(batch)
        db.commit()
        identifier = batch.id
    with pytest.raises(InvalidToken):
        validate_external_references(engine,tmp_path,secret_key=key)
    with Session(engine) as db:
        row = db.get(ProcessingBatch,identifier)
        row.policy = {"models":[{"encrypted_api_key":Fernet(key).encrypt(b"private frozen provider").decode()}],
                      "input_assets":{f"projects/{project_id}/missing-stage-mask.png":"digest"}}
        db.commit()
    report = validate_external_references(engine,tmp_path,secret_key=key)
    assert report["encrypted_keys_verified"] == 2 and not report["verified"]
    assert report["missing_assets"] == [f"projects/{project_id}/missing-stage-mask.png"]
    engine.dispose()


def test_asset_validation_resolves_repeated_history_references_once(tmp_path, monkeypatch):
    from pathlib import Path

    engine = create_database_engine(f"sqlite:///{tmp_path / 'repeated-assets.db'}")
    upgrade_database(engine)
    key, project_id = seed(engine, tmp_path)
    relative = f"projects/{project_id}/original.png"
    with Session(engine) as db:
        db.get(Project, project_id).settings = {"repeated": [relative] * 1000}
        db.commit()
    original_resolve, calls = Path.resolve, []

    def counted(path, *args, **kwargs):
        if path == (tmp_path / relative).parent:
            calls.append(path)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", counted)
    result = validate_external_references(engine, tmp_path, secret_key=key)
    assert result["verified"] and len(calls) == 1
    engine.dispose()


@pytest.mark.parametrize("kind", ["directory", "file"])
@pytest.mark.parametrize("namespace", ["projects", "fonts"])
def test_asset_validation_cached_parents_still_reject_escaping_symlinks(tmp_path, kind, namespace):
    root = tmp_path / "data"
    engine = create_database_engine(f"sqlite:///{tmp_path / 'symlink-assets.db'}")
    upgrade_database(engine)
    key, project_id = seed(engine, root)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.png").write_bytes(b"outside data root")
    link = root / namespace / "escape"
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(outside if kind == "directory" else outside / "file.png", target_is_directory=kind == "directory")
    relative = link.relative_to(root).as_posix() + ("/file.png" if kind == "directory" else "")
    with Session(engine) as db:
        db.get(Project, project_id).settings = {"unsafe": [relative] * 10}
        db.commit()
    with pytest.raises(ValueError, match="escapes"):
        validate_external_references(engine, root, secret_key=key)
    engine.dispose()


def test_asset_validation_includes_global_custom_fonts(tmp_path):
    import hashlib

    engine = create_database_engine(f"sqlite:///{tmp_path / 'font-assets.db'}")
    upgrade_database(engine)
    key, project_id = seed(engine, tmp_path)
    font = "fonts/custom.ttf"
    (tmp_path / "fonts").mkdir()
    (tmp_path / font).write_bytes(b"custom font bytes")
    with Session(engine) as db:
        region = db.scalar(select(TextRegion))
        region.layout_data = {"custom_font_path": font}
        db.get(Project, project_id).settings = {"fonts": [{"path": "fonts/missing.otf"}, {"path": font}]}
        db.commit()
    report = validate_external_references(engine, tmp_path, secret_key=key)
    assert report["assets_checked"] == 3
    assert not report["verified"] and report["missing_assets"] == ["fonts/missing.otf"]
    expected = hashlib.sha256()
    for relative in sorted([font, f"projects/{project_id}/original.png"]):
        digest = hashlib.sha256((tmp_path / relative).read_bytes()).hexdigest()
        expected.update(f"{relative}\0{digest}\n".encode())
    assert report["asset_manifest_sha256"] == expected.hexdigest()
    engine.dispose()


def test_asset_verification_parallel_io_keeps_manifest_order_and_bounds_workers(tmp_path, monkeypatch):
    import hashlib
    import threading
    import time

    from app.services import database_transfer

    engine = create_database_engine(f"sqlite:///{tmp_path / 'parallel-assets.db'}")
    upgrade_database(engine)
    key, project_id = seed(engine, tmp_path)
    contents = {f"projects/{project_id}/original.png": b"unchanged image bytes"}
    for index in range(8):
        relative = f"projects/{project_id}/extra-{index}.png"
        contents[relative] = str(index).encode()
        (tmp_path / relative).write_bytes(contents[relative])
    with Session(engine) as db:
        db.get(Project, project_id).settings = {"files": list(contents)}
        db.commit()
    digest, active, peak = database_transfer.file_digest, 0, 0
    lock = threading.Lock()

    def counted(path):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
        try:
            time.sleep(.02)
            return digest(path)
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(database_transfer, "file_digest", counted)
    result = validate_external_references(engine, tmp_path, secret_key=key)
    expected = hashlib.sha256()
    for relative, content in sorted(contents.items()):
        expected.update(f"{relative}\0{hashlib.sha256(content).hexdigest()}\n".encode())
    assert result["verified"] and result["asset_manifest_sha256"] == expected.hexdigest()
    assert 2 <= peak <= 4 and active == 0
    engine.dispose()

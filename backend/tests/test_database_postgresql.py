"""Real-engine concurrency/transaction checks; no external API calls."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.core.db_engine import create_database_engine
from app.core.migrations import upgrade_database
from app.core.transactions import has_pending_writes, is_retryable_write, retry_write
from app.models import EditorHistoryEntry, ImagePage, ProcessingTask, Project, TextRegion
from app.services.editor_history import begin_task_history, finish_task_history
from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


def test_read_transactions_are_clean_but_flushed_core_and_raw_writes_are_not(target):
    upgrade_database(target)
    with Session(target) as db:
        db.execute(select(Project))
        assert not has_pending_writes(db)
        project = Project(name="pending")
        db.add(project)
        assert has_pending_writes(db)
        db.flush()
        assert has_pending_writes(db)
        with pytest.raises(ValueError, match="clean session"):
            retry_write(db, lambda: None)
        db.commit()
        identifier = project.id
        assert not has_pending_writes(db)
        db.execute(update(Project).where(Project.id == identifier).values(name="core"))
        assert has_pending_writes(db)
        db.rollback()
        db.execute(text("UPDATE projects SET name='raw' WHERE id=:id"), {"id": identifier})
        assert has_pending_writes(db)
        db.rollback()
        assert retry_write(db, lambda: db.scalar(select(Project.name))) == "pending"


def test_nested_retry_is_rejected_and_outer_write_is_rolled_back(target):
    upgrade_database(target)
    with Session(target) as db:
        def outer():
            db.add(Project(name="must roll back"))
            retry_write(db, lambda: None)
        with pytest.raises(ValueError, match="must not nest"):
            retry_write(db, outer)
        assert db.scalar(select(Project)) is None
        assert retry_write(db, lambda: 42) == 42


def test_tied_reading_order_survives_updates_and_history_on_postgresql(target):
    from datetime import datetime, timedelta, timezone

    from app.api.regions import list_regions
    from app.services.editor_history import _page_state

    upgrade_database(target)
    with Session(target) as db:
        project = Project(name="stable reading order")
        db.add(project)
        db.flush()
        page = ImagePage(project_id=project.id, filename="page.png", original_path="missing.png", width=10, height=10)
        db.add(page)
        db.flush()
        first = datetime(2026, 10, 18, tzinfo=timezone.utc)
        regions = [TextRegion(id=str(3-index), image_id=page.id, region_key=f"R{index+1:03d}",
                              reading_order=0, created_at=first + timedelta(seconds=index)) for index in range(3)]
        db.add_all(regions)
        db.commit()
        expected = [region.id for region in regions]
        regions[0].source_text = "moving a physical database row must not reorder it"
        db.commit()
        db.expire(page, ["regions"])
        assert [region.id for region in page.regions] == expected
        assert [str(region.id) for region in list_regions(page.id, db)] == expected
        assert [region["id"] for region in _page_state(db, page)["regions"]] == expected


def test_postgresql_lock_timeout_retries_transaction_and_restores_local_timeout(target):
    upgrade_database(target)
    with Session(target) as db:
        project = Project(name="before")
        db.add(project)
        db.commit()
        identifier = project.id
    writer = target.connect()
    writer.execute(update(Project).where(Project.id == identifier).values(name="held"))
    attempted, retrying = threading.Event(), threading.Event()
    calls = []

    def work():
        with Session(target) as db:
            def save():
                calls.append(1)
                attempted.set()
                if len(calls) > 1:
                    retrying.set()
                db.execute(update(Project).where(Project.id == identifier).values(name="after"))
            retry_write(db, save, lock_timeout_ms=100)
            assert db.scalar(text("SHOW lock_timeout")) == "0"
    try:
        with ThreadPoolExecutor(1) as pool:
            future = pool.submit(work)
            assert attempted.wait(2)
            assert retrying.wait(3)
            writer.commit()
            future.result(5)
        with Session(target) as db:
            assert db.get(Project, identifier).name == "after"
    finally:
        writer.close()


def test_postgresql_pool_recovers_dead_idle_and_inflight_connections(target):
    upgrade_database(target)
    with Session(target) as db:
        project = Project(name="committed before disconnect")
        db.add(project)
        db.commit()
        identifier = project.id
    # A separate physical pool is essential: the admin must not check out
    # the very idle connection it is about to terminate.
    admin = create_database_engine(target.url)
    try:
        with target.connect() as idle:
            dead_pid = idle.scalar(text("SELECT pg_backend_pid()"))
        with admin.begin() as killer:
            assert killer.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": dead_pid})
        with target.connect() as recovered:
            assert recovered.scalar(text("SELECT pg_backend_pid()")) != dead_pid
            assert recovered.scalar(text("SELECT 1")) == 1

        # A connection lost in a transaction is NOT an automatic replayable
        # commit. Its uncommitted edit must disappear, then a fresh unit of
        # work can use the pool again without a poisoned Session.
        with target.connect() as writer:
            pid = writer.scalar(text("SELECT pg_backend_pid()"))
            writer.execute(update(Project).where(Project.id == identifier).values(name="never committed"))
            with admin.begin() as killer:
                assert killer.scalar(text("SELECT pg_terminate_backend(:pid)"), {"pid": pid})
            with pytest.raises(OperationalError) as error:
                writer.execute(text("SELECT 1"))
            assert not is_retryable_write(error.value)
            writer.rollback()
        with Session(target) as db:
            assert db.get(Project, identifier).name == "committed before disconnect"
            retry_write(db, lambda: db.execute(update(Project).where(
                Project.id == identifier).values(name="new explicit transaction")))
            assert db.get(Project, identifier).name == "new explicit transaction"
    finally:
        admin.dispose()


def test_invalidated_connection_rolls_back_without_boundary_event_reconnect(target):
    with target.connect() as connection:
        connection.execute(text("SELECT 1"))
        connection.invalidate()
        connection.rollback()
        assert not connection.in_transaction()
        assert connection.scalar(text("SELECT 1")) == 1


def test_parallel_journals_keep_all_entries_and_page_fields(target, monkeypatch):
    upgrade_database(target)
    # Archive no real files: input paths deliberately do not exist in test data.
    with Session(target) as db:
        project = Project(name="concurrent pages")
        db.add(project)
        db.flush()
        ids = []
        for index in range(8):
            page = ImagePage(project_id=project.id, filename=f"{index}.png", original_path=f"missing-{index}.png",
                             width=100, height=100, order_index=index)
            db.add(page)
            db.flush()
            db.add(TextRegion(image_id=page.id, region_key="R001"))
            task = ProcessingTask(project_id=project.id, image_id=page.id, task_type="ocr", status="RUNNING")
            db.add(task)
            db.flush()
            ids.append(task.id)
        db.commit()
    barrier = threading.Barrier(8)

    def work(task_id):
        with Session(target) as db:
            task = db.get(ProcessingTask, task_id)
            entry_id = begin_task_history(db, task)
            barrier.wait(10)
            def save():
                current = db.get(ProcessingTask, task_id)
                current.page.regions[0].source_text = task_id
            retry_write(db, save)
            finish_task_history(db, task_id, entry_id)
            return entry_id

    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(work, ids))
    assert len(set(results)) == 8
    with Session(target) as db:
        entries = list(db.scalars(select(EditorHistoryEntry)))
        assert len(entries) == 8 and all(not entry.pending for entry in entries)
        assert {entry.after["regions"][0]["source_text"] for entry in entries} == set(ids)


@pytest.mark.parametrize("code,expected", [("40001", True), ("40P01", True), ("55P03", True),
                                           ("08006", False), ("23505", False), (None, False)])
def test_retry_only_known_transaction_aborts_not_unknown_commit(code, expected):
    class Error(Exception):
        sqlstate = code
    assert is_retryable_write(OperationalError("test", {}, Error())) == expected

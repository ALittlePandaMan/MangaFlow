"""Connection-private stage tables must never expose or alter published rows."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from time import monotonic, sleep

import pytest
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.db_engine import create_database_engine, read_only_connection
from app.models import Project
from app.services.workflow_state import capture_page, private_page
from sqlalchemy import select, text
from sqlalchemy.exc import InternalError, ProgrammingError
from test_repair_geometry_sync import setup_page


def source_state(client):
    page, _ = setup_page(client)
    with SessionLocal() as db:
        return capture_page(db, page["id"])


def assert_temporary_tables_removed(namespace):
    # libpq closes its socket before the server finishes disconnect cleanup.
    # Wait for the server's catalog change, not just the local close() return.
    deadline = monotonic() + 3
    with SessionLocal() as db:
        while True:
            count = db.scalar(text("SELECT count(*) FROM pg_class WHERE relnamespace=:namespace"),
                              {"namespace": namespace})
            if count == 0:
                return
            assert monotonic() < deadline, f"Temporary tables survived connection closure: {count}"
            db.rollback()
            sleep(.02)


def test_committed_private_edits_and_rollbacks_never_reach_the_source(client):
    state = source_state(client)
    with private_page(state) as (db, page):
        assert db.bind.dialect.name == "postgresql"
        assert db.scalar(text("SHOW search_path")) == "pg_temp"
        project = db.get(Project, page.project_id)
        project.name = "only in private connection"
        db.commit()
        db.rollback()
        assert db.get(Project, page.project_id).name == "only in private connection"
        with SessionLocal() as source:
            assert source.get(Project, page.project_id).name == state["project"]["name"]
        namespace = db.scalar(text("SELECT pg_my_temp_schema()"))
        physical = db.connection().connection.driver_connection
    assert physical.closed
    assert_temporary_tables_removed(namespace)


def test_missing_private_table_cannot_fall_back_to_the_published_table(client):
    state = source_state(client)
    with private_page(state) as (db, _):
        db.execute(text("DROP TABLE pg_temp.projects CASCADE"))
        db.commit()
        with pytest.raises(ProgrammingError, match="does not exist"):
            db.execute(select(Project))
        db.rollback()
    with SessionLocal() as source:
        assert source.get(Project, state["project"]["id"]) is not None


def test_parallel_private_branches_with_identical_ids_remain_isolated(client):
    state = source_state(client)
    barrier = Barrier(2)

    def work(name):
        with private_page(state) as (db, page):
            db.get(Project, page.project_id).name = name
            db.commit()
            barrier.wait(5)
            return db.get(Project, page.project_id).name

    with ThreadPoolExecutor(2) as pool:
        assert list(pool.map(work, ("translation branch", "repair branch"))) == ["translation branch", "repair branch"]


def test_failed_private_stage_closes_and_removes_its_temporary_tables(client):
    state = source_state(client)
    with pytest.raises(ValueError, match="stage failed"):
        with private_page(state) as (db, _):
            namespace = db.scalar(text("SELECT pg_my_temp_schema()"))
            physical = db.connection().connection.driver_connection
            raise ValueError("stage failed")
    assert physical.closed
    assert_temporary_tables_removed(namespace)


def test_diagnostic_snapshots_reject_writes(client):
    with read_only_connection(get_settings().database_url) as connection:
        with pytest.raises(InternalError, match="read-only"):
            connection.exec_driver_sql("CREATE TABLE forbidden_test_write (id integer)")


@pytest.mark.parametrize("url", ["mysql://user@localhost/db", "postgresql+psycopg2://user@localhost/db"])
def test_engine_rejects_unsupported_database_drivers(url):
    with pytest.raises(ValueError, match="Only PostgreSQL"):
        create_database_engine(url)

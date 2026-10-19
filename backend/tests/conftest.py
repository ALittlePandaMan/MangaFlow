from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DATA = Path(tempfile.mkdtemp(prefix="mangaflow-tests-"))
os.environ["MANGAFLOW_DATA_DIR"] = str(TEST_DATA)
app_postgres_url = os.environ.get("MANGAFLOW_TEST_APP_POSTGRES_URL")
if app_postgres_url:
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.engine import make_url

    parsed = make_url(app_postgres_url)
    if parsed.drivername != "postgresql+psycopg" or not (parsed.database or "").startswith("mangaflow_test_"):
        raise ValueError("App integration tests require a dedicated mangaflow_test_* PostgreSQL database")
    probe = create_engine(parsed)
    try:
        if inspect(probe).get_table_names():
            raise ValueError("App integration test database must be empty; existing data is never erased")
    finally:
        probe.dispose()
os.environ["MANGAFLOW_DATABASE_URL"] = app_postgres_url or f"sqlite:///{TEST_DATA / 'test.db'}"
os.environ["MANGAFLOW_TASK_CONCURRENCY"] = "1"
os.environ["MANGAFLOW_MODEL_DIR"] = str(TEST_DATA / "models")
os.environ["MANGAFLOW_MODEL_MANIFEST_PATH"] = ""
os.environ["MANGAFLOW_ENVIRONMENT_FILE_PATH"] = str(TEST_DATA / ".env")

from app.main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client

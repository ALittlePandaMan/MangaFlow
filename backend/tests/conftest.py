from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from app.core.config import Settings
from app.core.db_engine import create_database_engine
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

# Test URLs must be explicit and must never refer to an application database.
raw_url = os.environ.get("MANGAFLOW_TEST_POSTGRES_URL")
if not raw_url:
    raise pytest.UsageError("Set MANGAFLOW_TEST_POSTGRES_URL to a dedicated mangaflow_test_* PostgreSQL database")
TEST_URL = make_url(raw_url)
if TEST_URL.drivername != "postgresql+psycopg" or not (TEST_URL.database or "").startswith("mangaflow_test_"):
    raise pytest.UsageError("Tests require a dedicated mangaflow_test_* PostgreSQL database with psycopg 3")
admin = create_database_engine(TEST_URL)
app_schema = "app_test_" + uuid4().hex
with admin.begin() as connection:
    connection.exec_driver_sql(f'CREATE SCHEMA "{app_schema}"')

TEST_DATA = Path(tempfile.mkdtemp(prefix="mangaflow-tests-"))
Settings.model_config["env_file"] = None  # Never inherit the user's private configuration.
os.environ["MANGAFLOW_DATABASE_URL"] = TEST_URL.update_query_dict({
    "options": f"-c timezone=UTC -c search_path={app_schema}",
}).render_as_string(hide_password=False)
os.environ["MANGAFLOW_DATA_DIR"] = str(TEST_DATA)
os.environ["MANGAFLOW_TASK_CONCURRENCY"] = "1"
os.environ["MANGAFLOW_AUTO_PROVISION_MODELS"] = "false"
os.environ["MANGAFLOW_MODEL_DIR"] = str(TEST_DATA / "models")
os.environ["MANGAFLOW_MODEL_MANIFEST_PATH"] = ""
os.environ["MANGAFLOW_ENVIRONMENT_FILE_PATH"] = str(TEST_DATA / ".env")

from app.core.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@contextmanager
def isolated_database():
    schema = "case_test_" + uuid4().hex
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    target = create_database_engine(TEST_URL, connect_args={
        "options": f"-c timezone=UTC -c search_path={schema}",
    })
    try:
        yield target
    finally:
        target.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')


@pytest.fixture
def target():
    with isolated_database() as database:
        yield database


@pytest.fixture
def model_engine(target):
    Base.metadata.create_all(target)
    return target


@pytest.fixture(scope="session", autouse=True)
def application_database():
    try:
        yield
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{app_schema}" CASCADE')
        admin.dispose()


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client

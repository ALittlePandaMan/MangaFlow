from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

TEST_DATA = Path(tempfile.mkdtemp(prefix="mangaflow-tests-"))
os.environ["MANGAFLOW_DATA_DIR"] = str(TEST_DATA)
os.environ["MANGAFLOW_DATABASE_URL"] = f"sqlite:///{TEST_DATA / 'test.db'}"
os.environ["MANGAFLOW_TASK_CONCURRENCY"] = "1"

from app.main import app  # noqa: E402


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client

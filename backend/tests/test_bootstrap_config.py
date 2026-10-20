"""First-run config creates private credentials without evaluating user content."""
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("bash") is None or shutil.which("openssl") is None,
                                reason="Bash startup helpers require Bash and OpenSSL")


def prepare(root):
    environment = {key: value for key, value in os.environ.items() if key != "MANGAFLOW_POSTGRES_PASSWORD"}
    subprocess.run(["bash", "-c", 'repo_dir="$1"; source "$2"', "prepare", str(root),
                    str(ROOT / "scripts/prepare-config.sh")], check=True, env=environment, capture_output=True)


@pytest.mark.parametrize("value", ["", '""', "''", '"" # empty password'])
def test_empty_password_generates_once_without_evaluating_the_environment(tmp_path, value):
    marker = tmp_path / "must-not-exist"
    template = f'MANGAFLOW_POSTGRES_PASSWORD={value}\nUNRELATED=$(touch "{marker}")\n'
    (tmp_path / ".env.example").write_text(template)
    (tmp_path / "config.example.yaml").write_text("schema_version: 1\n")
    prepare(tmp_path)
    actual = (tmp_path / ".env").read_text()
    assert re.search(r"^MANGAFLOW_POSTGRES_PASSWORD=[0-9a-f]{64}$", actual, re.MULTILINE)
    assert not marker.exists()
    prepare(tmp_path)
    assert (tmp_path / ".env").read_text() == actual


@pytest.mark.parametrize("value", ["existing-password", '"existing-password"'])
def test_existing_private_password_and_model_config_are_preserved(tmp_path, value):
    content = f"MANGAFLOW_POSTGRES_PASSWORD={value}\n"
    (tmp_path / ".env").write_text(content)
    (tmp_path / "config.yaml").write_text("custom: keep\n")
    prepare(tmp_path)
    assert (tmp_path / ".env").read_text() == content
    assert (tmp_path / "config.yaml").read_text() == "custom: keep\n"

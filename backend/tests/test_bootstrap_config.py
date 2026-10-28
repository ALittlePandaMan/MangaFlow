"""First-run config creates private credentials without evaluating user content."""
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
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


@pytest.mark.skipif(os.name != "posix", reason="Bash development process lifecycle")
@pytest.mark.parametrize("failure,expected", [("backend", 7), ("frontend", 9), ("signal", 143)])
def test_development_startup_stops_its_sibling_process(tmp_path, failure, expected):
    """Fake model/frontend processes exercise real launcher traps and wait logic."""
    for folder in ("scripts", "backend", ".venv/bin", "frontend/node_modules", "bin"):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "scripts/dev.sh", tmp_path / "scripts/dev.sh")
    (tmp_path / "scripts/prepare-config.sh").write_text(":\n")
    program = '''#!/usr/bin/env bash
set -eu
if [[ "${1:-}" == *devServer.mjs ]]; then printf '18000 15173\\n'; exit; fi
name=backend
status=7
other=frontend
if [[ "$0" == */node ]]; then name=frontend; status=9; other=backend; fi
printf '%s\\n' "$@" > "$TEST_ROOT/$name.args"
trap 'touch "$TEST_ROOT/$name.stopped"; exit 0' TERM INT
touch "$TEST_ROOT/$name.ready"
if [[ "$FAILURE" == "$name" ]]; then
  while [[ ! -f "$TEST_ROOT/$other.ready" ]]; do sleep .02; done
  exit "$status"
fi
while :; do sleep .02; done
'''
    for relative in ("bin/node", ".venv/bin/python"):
        target = tmp_path / relative
        cleanup = ("#!/usr/bin/env bash\nif [[ \"${2:-}\" == --stop ]]; then shift; exec "
                   + shlex.quote(sys.executable) + " " + shlex.quote(str(ROOT / "scripts/dev_backend.py"))
                   + ' "$@"; fi\n')
        target.write_text(cleanup + program)
        target.chmod(0o755)
    environment = {**os.environ, "TEST_ROOT": str(tmp_path), "FAILURE": failure,
                   "PATH": str(tmp_path / "bin") + os.pathsep + os.environ["PATH"]}
    process = subprocess.Popen(["bash", str(tmp_path / "scripts/dev.sh")], env=environment,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        deadline = time.monotonic() + 8
        while not all((tmp_path / f"{name}.ready").exists() for name in ("backend", "frontend")):
            if process.poll() is not None or time.monotonic() > deadline:
                pytest.fail("Development launcher did not start both test processes")
            time.sleep(.02)
        if failure == "signal":
            process.terminate()
        _, error = process.communicate(timeout=8)
        assert process.returncode == expected, error.decode()
        for name in ("backend", "frontend"):
            if name != failure:
                assert (tmp_path / f"{name}.stopped").exists()
        assert (tmp_path / "backend.args").read_text().splitlines()[-2:] == ["--port", "18000"]
        assert (tmp_path / "frontend.args").read_text().splitlines()[-3:] == ["--port", "15173", "--strictPort"]
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()

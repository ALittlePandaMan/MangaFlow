"""Exercise actual Uvicorn workers, reloads and the development launcher."""
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import psutil
import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != "posix", reason="Bash/WSL development launcher")


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def wait_for(check, process, log, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        if process.poll() is not None:
            pytest.fail(log.read_text())
        time.sleep(.05)
    pytest.fail("Development process timed out: " + log.read_text())


@pytest.mark.parametrize("scenario", ["import", "startup", "occupied", "reload", "slow_startup", "worker_exit"])
def test_real_development_backend_failure_reload_and_cleanup(tmp_path, scenario):
    for folder in ("scripts", "backend/app", ".venv/bin", "frontend/node_modules", "bin"):
        (tmp_path / folder).mkdir(parents=True, exist_ok=True)
    for script in ("dev.sh", "dev_backend.py"):
        shutil.copyfile(ROOT / "scripts" / script, tmp_path / "scripts" / script)
    (tmp_path / "scripts/prepare-config.sh").write_text(":\n")
    python = tmp_path / ".venv/bin/python"
    python.write_text("#!/usr/bin/env bash\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    python.chmod(0o755)
    port = free_port()
    node = tmp_path / "bin/node"
    node.write_text(f'''#!/usr/bin/env bash
if [[ "${{1:-}}" == *devServer.mjs ]]; then printf '{port} 15173\\n'; exit; fi
trap 'touch "$TEST_ROOT/frontend.stopped"; exit' TERM INT
touch "$TEST_ROOT/frontend.started"
while :; do sleep .05; done
''')
    node.chmod(0o755)
    app = tmp_path / "backend/app/main.py"
    source = '''import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
@asynccontextmanager
async def lifespan(app):
    Path("worker.pid").write_text(str(os.getpid()))
    STARTUP
    yield
app = FastAPI(lifespan=lifespan)
@app.get("/")
def read():
    return {"pid": os.getpid(), "value": "first"}
'''
    if scenario == "import":
        source = "raise RuntimeError('isolated import failure')\n"
    else:
        source = source.replace("STARTUP", "raise RuntimeError('isolated startup failure')" if scenario == "startup"
                                else "await asyncio.sleep(2)" if scenario == "slow_startup" else "pass")
    app.write_text(source)
    reservation = socket.socket()
    if scenario == "occupied":
        reservation.bind(("127.0.0.1", port))
        reservation.listen()
    log = tmp_path / "server.log"
    process = None
    children = []
    try:
        with log.open("w") as output:
            process = subprocess.Popen(["bash", str(tmp_path / "scripts/dev.sh")], cwd=tmp_path,
                env={**os.environ, "TEST_ROOT": str(tmp_path), "PATH": str(tmp_path / "bin") + os.pathsep + os.environ["PATH"]},
                stdout=output, stderr=output, start_new_session=True)
            if scenario in {"import", "startup", "occupied"}:
                process.wait(timeout=15)
                assert process.returncode != 0
                assert (tmp_path / "frontend.stopped").exists(), log.read_text()
                return

            def response():
                try:
                    import json
                    return json.load(urlopen(f"http://127.0.0.1:{port}/", timeout=.3))
                except OSError:
                    return None

            wait_for(response, process, log)
            original = response()
            children = psutil.Process(process.pid).children(recursive=True)
            if scenario == "worker_exit":
                os.kill(original["pid"], signal.SIGTERM)
                process.wait(timeout=35)
                assert process.returncode != 0
                assert (tmp_path / "frontend.stopped").exists()
                return
            if scenario == "reload":
                app.write_text(source.replace('"first"', '"second"'))
                wait_for(lambda: (value := response()) and value["value"] == "second", process, log)
                assert response()["pid"] != original["pid"]
            process.terminate()
            process.wait(timeout=35)
            assert process.returncode == 143
            assert (tmp_path / "frontend.stopped").exists()
            for child in children:
                assert not child.is_running() or child.status() == psutil.STATUS_ZOMBIE
            with pytest.raises(OSError):
                urlopen(f"http://127.0.0.1:{port}/", timeout=.3)
    finally:
        reservation.close()
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)

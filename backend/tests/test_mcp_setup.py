import hashlib
import io
import tarfile

import pytest


@pytest.fixture
def client(client):
    client.headers["Host"] = "localhost:8080"
    return client


def test_public_installation_is_complete_and_deterministic(client):
    from app.services.mcp_setup import CONNECTOR_FILES

    manifest = client.get("/mangaflow/setup.json")
    assert manifest.status_code == 200
    metadata = manifest.json()
    assert metadata["endpoint"] == "http://localhost:8080/mangaflow"
    package = client.get(metadata["package_path"])
    assert package.content == client.get(metadata["package_path"]).content
    assert len(package.content) == metadata["size"]
    assert hashlib.sha256(package.content).hexdigest() == metadata["sha256"]
    with tarfile.open(fileobj=io.BytesIO(package.content), mode="r:gz") as archive:
        assert archive.getnames() == ["package/" + name for name in CONNECTOR_FILES]
        assert all(entry.isfile() for entry in archive)
    guide = client.get("/mangaflow/setup")
    assert guide.headers["content-type"].startswith("text/markdown")
    for step in (metadata["installer_url"], "npm ci --ignore-scripts", "Preserve existing MCP servers",
                 "local_import", "current Agent session has reconnected"):
        assert step in guide.text
    installer = client.get("/mangaflow/install.mjs")
    assert installer.status_code == 200
    assert "Standalone bootstrap" in installer.text
    token = client.get("/api/mcp-settings").json()["token"]
    assert token not in guide.text + manifest.text + installer.text


def test_remote_bootstrap_requires_no_key_but_business_and_transfers_still_do(client):
    client.put("/api/mcp-settings", json={"public_url": "https://manga.example/mangaflow"})
    try:
        remote = {"Host": "manga.example"}
        for path in ("setup", "setup.json", "install.mjs", "connector.tgz"):
            assert client.get("/mangaflow/" + path, headers=remote).status_code == 200
        assert "https://manga.example/mangaflow/install.mjs" in client.get("/mangaflow/setup", headers=remote).text
        for path in ("/mangaflow", "/mangaflow/files", "/mangaflow/download/invalid", "/mcp"):
            assert client.post(path, headers=remote).status_code == 401
        assert client.get("/mangaflow/setup", headers={"Host": "evil.invalid"}).status_code == 403
        assert client.get("/mangaflow/setup", headers={**remote, "Origin": "https://evil.invalid"}).status_code == 403
        # Prefix matching must never turn unrelated paths/methods into public APIs.
        assert client.get("/mangaflow/setup/secret", headers=remote).status_code == 401
        assert client.post("/mangaflow/setup", headers=remote).status_code == 401
        client.put("/api/mcp-settings", json={"enabled": False})
        assert client.get("/mangaflow/setup", headers=remote).status_code == 200
        assert client.post("/mangaflow", headers=remote).status_code == 503
    finally:
        client.put("/api/mcp-settings", json={"public_url": "", "enabled": True})


def test_setup_material_is_available_at_legacy_alias_and_in_manual_zip(client):
    from zipfile import ZipFile

    for path in ("setup", "setup.json", "install.mjs", "connector.tgz"):
        assert client.get("/mcp/" + path).status_code == 200
    with ZipFile(io.BytesIO(client.get("/api/mcp-settings/connector").content)) as archive:
        assert "mangaflow-mcp/install.mjs" in archive.namelist()


def test_installer_downloads_installs_reuses_and_probes_real_stdio(tmp_path):
    """One-time install from a real HTTP server, with no pre-existing connector install."""
    import json
    import os
    import shutil
    import socket
    import subprocess
    import threading
    import time
    from pathlib import Path

    import httpx
    import uvicorn
    from app.main import app

    node = shutil.which("node")
    if not node or not shutil.which("npm"):
        pytest.skip("Node.js/npm needed for the installation integration")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{port}/mangaflow"
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(.05)
        assert server.started
        installer = tmp_path / "official-install.mjs"
        installer.write_bytes(httpx.get(endpoint + "/install.mjs").content)
        directory = tmp_path / "Agent 安装 有空格"
        command = [node, str(installer), "--url", endpoint, "--directory", str(directory)]
        results = []
        for index in range(2):
            # Explicit --url overrides an unrelated environment value; an
            # environment-only reinstall must preserve the actual custom port.
            arguments = command if index == 0 else [node, str(installer), "--directory", str(directory)]
            environment = {**os.environ, "MANGAFLOW_MCP_URL": "http://localhost:1/mangaflow" if index == 0 else endpoint}
            installed = subprocess.run(arguments, capture_output=True, text=True, timeout=180, env=environment)
            assert installed.returncode == 0, installed.stderr
            result = json.loads(installed.stdout)
            assert result["installed"] and result["probe"]["verified"]
            assert result["probe"]["connection_capabilities"]["local_import"]
            assert result["server"]["env"] == {"MANGAFLOW_MCP_URL": endpoint}
            assert result["server"]["command"] == str(Path(node).resolve())
            assert directory in Path(result["server"]["args"][0]).parents
            assert "MANGAFLOW_MCP_TOKEN" not in installed.stdout
            results.append(result)
        assert not results[0]["reused"] and results[1]["reused"]
        assert results[0]["directory"] == results[1]["directory"]
        assert not (directory / "install.lock").exists()
        # An interrupted npm install may leave source and SDK files, but never
        # a success receipt. The next run must repair rather than reuse it.
        (Path(results[0]["directory"]) / "installed.json").unlink()
        repaired = subprocess.run(command, capture_output=True, text=True, timeout=180)
        assert repaired.returncode == 0, repaired.stderr
        repaired_result = json.loads(repaired.stdout)
        assert repaired_result["probe"]["verified"] and not repaired_result["reused"]
    finally:
        server.should_exit = True
        thread.join(15)
        sock.close()

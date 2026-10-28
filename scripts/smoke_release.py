"""Isolated deployment smoke test; never targets an existing Compose project."""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.request import Request, urlopen
from zipfile import ZipFile


def request(url: str, body: dict | None = None, headers: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    with urlopen(Request(url, data=data, headers={"Content-Type": "application/json", **(headers or {})}), timeout=20) as response:
        content = response.read().decode()
        if "text/event-stream" in response.headers.get("Content-Type", ""):
            content = next(line[6:] for line in content.splitlines() if line.startswith("data: "))
        return json.loads(content), dict(response.headers)


def smoke(archive: Path, images: dict[str, str], *, anonymous: bool = True) -> None:
    with tempfile.TemporaryDirectory(prefix="mangaflow-release-smoke-") as temporary:
        root = Path(temporary)
        with ZipFile(archive) as source:
            # The release packager uses only fixed public member names.
            for name in source.namelist():
                if not (root / name).resolve().is_relative_to(root):
                    raise ValueError("Unsafe deployment ZIP")
            source.extractall(root)
        deployment, = root.glob("mangaflow-docker-*")
        project = "mangaflow-release-test-" + secrets.token_hex(5)
        environment = {key: value for key, value in os.environ.items() if not key.startswith("MANGAFLOW_")}
        # Strip provider credentials as well; this run performs no model work.
        environment.pop("PGPASSWORD", None)
        docker_prefix = ["docker"]
        if anonymous:
            docker_config = root / "anonymous-docker"
            docker_config.mkdir()
            docker_prefix += ["--config", str(docker_config)]
            for image in images.values():
                subprocess.run([*docker_prefix, "pull", image], check=True, env=environment)
        # Exercise the shipped variables, rather than replacing Compose ports.
        reservations = [socket.socket(), socket.socket()]
        try:
            for reservation in reservations:
                reservation.bind(("127.0.0.1", 0))
            web_port, api_port = [reservation.getsockname()[1] for reservation in reservations]
        finally:
            for reservation in reservations:
                reservation.close()
        (deployment / ".env").write_text(
            f"MANGAFLOW_POSTGRES_PASSWORD={secrets.token_hex(24)}\nMANGAFLOW_AUTO_PROVISION_MODELS=false\n"
            f"MANGAFLOW_WEB_PORT={web_port}\nMANGAFLOW_API_PORT={api_port}\n", encoding="utf-8")
        (deployment / "config.yaml").write_bytes((deployment / "config.example.yaml").read_bytes())
        # Docker creates absent bind-mount directories as root on Linux.
        # Keep the mount roots writable by this test's host user.
        for folder in ("data", "models"):
            (deployment / folder).mkdir()
        override = deployment / "smoke.override.yml"
        override.write_text(
            "services:\n  backend:\n"
            f"    image: {images['backend']}\n    pull_policy: never\n    gpus: !reset []\n"
            "  frontend:\n"
            f"    image: {images['frontend']}\n    pull_policy: never\n", encoding="utf-8")
        compose = [*docker_prefix, "compose", "--project-name", project, "--project-directory", str(deployment),
                   "-f", str(deployment / "docker-compose.yml"), "-f", str(override)]

        def run(*args):
            return subprocess.check_output([*compose, *args], text=True, encoding="utf-8",
                                           env=environment, stderr=subprocess.STDOUT).strip()

        def port():
            return int(run("port", "frontend", "80").rsplit(":", 1)[1])

        try:
            run("up", "-d", "--wait", "--wait-timeout", "180")
            base = f"http://127.0.0.1:{port()}"
            assert port() == web_port
            with urlopen(base, timeout=20) as response:
                assert response.status == 200 and re.search(rb'<div\b[^>]*\bid="root"', response.read())
            backend_port = int(run("port", "backend", "8000").rsplit(":", 1)[1])
            assert backend_port == api_port
            health, _ = request(f"http://127.0.0.1:{backend_port}/health")
            assert health["status"] == "ok"
            assert health["version"] == (deployment / "VERSION").read_text().strip()
            manifest, _ = request(base + "/mangaflow/setup.json")
            assert manifest["endpoint"] == base + "/mangaflow"
            assert manifest["guide_url"] == base + "/mangaflow/setup"
            saved, _ = request(base + "/api/mcp-settings")
            assert saved["public_url"] == ""
            project_data, _ = request(base + "/api/projects", {"name": "Release persistence test"})
            identifier = project_data["id"]
            header = {"Accept": "application/json, text/event-stream"}
            _, response_headers = request(base + "/mangaflow", {"jsonrpc": "2.0", "id": 1,
                "method": "initialize", "params": {"protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "release-smoke", "version": "1"}}}, header)
            for key, value in response_headers.items():
                if key.lower() == "mcp-session-id":
                    header["Mcp-Session-Id"] = value
            header["MCP-Protocol-Version"] = "2025-11-25"
            tools, _ = request(base + "/mangaflow", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, header)
            assert len(tools["result"]["tools"]) == 20
            for folder in ("data", "models"):
                (deployment / folder / "release-smoke.txt").write_text(f"persistent-{folder}", encoding="utf-8")
            config_before = (deployment / "config.yaml").read_bytes()
            secret_before = (deployment / ".env").read_bytes()
            run("up", "-d", "--force-recreate", "--wait", "--wait-timeout", "180")
            base = f"http://127.0.0.1:{port()}"
            # Nginx may briefly cache the backend's previous container address.
            deadline = time.monotonic() + 20
            while True:
                try:
                    persisted, _ = request(base + f"/api/projects/{identifier}")
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(1)
            assert persisted["id"] == identifier
            for folder in ("data", "models"):
                run("exec", "-T", "backend", "python", "-c",
                    f"from pathlib import Path; assert Path('/{folder}/release-smoke.txt').read_text() == 'persistent-{folder}'")
            assert (deployment / "config.yaml").read_bytes() == config_before
            assert (deployment / ".env").read_bytes() == secret_before
            print(json.dumps({"project": project, "status": "passed", "tools": 20,
                              "persistence": True, "anonymous_pull": anonymous}))
        except Exception:
            # Avoid dumping environment/configuration or provider secrets.
            print(run("ps"))
            raise
        finally:
            # This random, private test project is created above; its volumes
            # are disposable and can never name the normal mangaflow project.
            try:
                if os.name == "posix":
                    # The backend also creates private, root-owned children.
                    # Stop writers, then restore ownership through this isolated
                    # Compose service's exact mounts before TemporaryDirectory
                    # cleans them. Do not relax secret permissions or follow links.
                    run("stop")
                    run("run", "--rm", "--no-deps", "--user", "0:0", "--entrypoint", "chown", "backend",
                        "-R", "--no-dereference", f"{os.getuid()}:{os.getgid()}", "/data", "/models")
            finally:
                run("down", "--volumes", "--remove-orphans")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--digest-dir", type=Path)
    parser.add_argument("--local-images", action="store_true", help="Verify locally built images without claiming anonymous pull")
    args = parser.parse_args()
    images = {}
    for service in ("backend", "frontend"):
        if args.local_images:
            images[service] = f"mangaflow-{service}:latest"
        else:
            if not args.digest_dir:
                parser.error("--digest-dir is required for registry verification")
            digest = (args.digest_dir / f"{service}.digest").read_text().strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
                parser.error("Invalid staged image digest")
            images[service] = f"ghcr.io/alittlepandaman/mangaflow-{service}@{digest}"
    smoke(args.archive, images, anonymous=not args.local_images)


if __name__ == "__main__":
    main()

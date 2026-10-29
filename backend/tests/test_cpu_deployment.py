"""Explicit CPU selection must reach real Compose without evaluating private config."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose CLI")
@pytest.mark.parametrize("release", [False, True])
def test_cpu_compose_keeps_ports_volumes_and_shared_frontend(tmp_path, release):
    base = "docker-compose.release.yml" if release else "docker-compose.yml"
    cpu = "docker-compose.release.cpu.yml" if release else "docker-compose.cpu.yml"
    shutil.copyfile(ROOT / base, tmp_path / "docker-compose.yml")
    shutil.copyfile(ROOT / cpu, tmp_path / "docker-compose.cpu.yml")
    (tmp_path / ".env").write_text("MANGAFLOW_POSTGRES_PASSWORD=isolated-config-only\n"
        "MANGAFLOW_IMAGE_TAG=9.8.7\nMANGAFLOW_WEB_PORT=18080\nMANGAFLOW_API_PORT=18000\nMANGAFLOW_RUNTIME=cpu\n")
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("MANGAFLOW_", "COMPOSE_"))}
    # Match docker.sh: an explicit process selection overrides the private file.
    environment["MANGAFLOW_RUNTIME"] = "gpu"
    command = ["docker", "compose", "--project-directory", str(tmp_path), "--env-file", str(tmp_path / ".env"),
               "-f", str(tmp_path / "docker-compose.yml")]
    gpu = json.loads(subprocess.check_output([*command, "config", "--format", "json"], env=environment))
    result = json.loads(subprocess.check_output([*command, "-f", str(tmp_path / "docker-compose.cpu.yml"),
                        "config", "--format", "json"], env=environment))
    backend = result["services"]["backend"]
    assert not backend.get("gpus")
    for field in ("ports", "volumes", "depends_on", "healthcheck"):
        assert backend[field] == gpu["services"]["backend"][field]
    assert result["services"]["frontend"] == gpu["services"]["frontend"]
    assert backend["environment"]["CUDA_VISIBLE_DEVICES"] == ""
    assert backend["environment"]["MANGAFLOW_SEGMENTATION_BACKEND"] == "opencv"
    assert backend["environment"]["MANGAFLOW_RUNTIME"] == "cpu"
    assert gpu["services"]["backend"]["environment"]["MANGAFLOW_RUNTIME"] == "gpu"
    if release:
        assert "build" not in backend
        assert backend["image"] == "ghcr.io/alittlepandaman/mangaflow-backend:9.8.7-cpu"
        assert result["services"]["frontend"]["image"].endswith(":9.8.7")
    else:
        assert backend["build"]["dockerfile"] == "docker/backend.cpu.Dockerfile"
        # A later switch to GPU must not reuse a CPU build under the implicit GPU tag.
        assert backend["image"] == "mangaflow-backend:cpu-local"
        assert backend.get("image") != gpu["services"]["backend"].get("image")


@pytest.fixture
def launcher(tmp_path):
    if os.name != "posix":
        pytest.skip("Bash launcher")
    for folder in ("scripts", "bin"):
        (tmp_path / folder).mkdir()
    for name in ("docker.sh", "prepare-config.sh"):
        shutil.copyfile(ROOT / "scripts" / name, tmp_path / "scripts" / name)
    for name in ("docker-compose.yml", "docker-compose.cpu.yml"):
        (tmp_path / name).write_text("services: {}\n")
    (tmp_path / "config.yaml").write_text("keep: private\n")
    docker = tmp_path / "bin/docker"
    docker.write_text("#!" + sys.executable + "\nimport json,os,sys\n"
        "with open(os.environ['CALLS'],'a') as f:f.write(json.dumps(sys.argv[1:])+'\\n')\n"
        "print(os.environ.get('PLATFORM','linux/x86_64') if sys.argv[1]=='info' else '{}')\n")
    docker.chmod(0o755)
    # The fixture uses Linux paths and a fake native Docker CLI, even under WSL.
    grep = tmp_path / "bin/grep"
    grep.write_text("#!/bin/sh\nexit 1\n")
    grep.chmod(0o755)
    environment = {key: value for key, value in os.environ.items() if not key.startswith("MANGAFLOW_")}
    environment.update(PATH=str(tmp_path / "bin") + os.pathsep + os.environ["PATH"], CALLS=str(tmp_path / "calls"))
    return tmp_path, environment


@pytest.mark.parametrize("file_value,override,expected", [
    (None, None, "gpu"), ("cpu", None, "cpu"), ('"cpu" # local choice', None, "cpu"),
    ("'gpu'", "cpu", "cpu"), ("cpu", "gpu", "gpu"), ("", None, None),
    ("$(touch injected)", None, None), ("gpu", "", None), ("other", None, None)])
def test_runtime_literal_precedence_and_no_evaluation(launcher, file_value, override, expected):
    root, environment = launcher
    private = "MANGAFLOW_POSTGRES_PASSWORD=isolated-config-only\nUNRELATED=$(touch injected)\n"
    if file_value is not None:
        private += "MANGAFLOW_RUNTIME=" + file_value + "\n"
    (root / ".env").write_text(private)
    if override is not None:
        environment["MANGAFLOW_RUNTIME"] = override
    result = subprocess.run(["bash", str(root / "scripts/docker.sh"), "config"], env=environment,
                            cwd=root, capture_output=True, text=True)
    assert not (root / "injected").exists()
    assert (root / ".env").read_text() == private
    assert (root / "config.yaml").read_text() == "keep: private\n"
    if expected is None:
        assert result.returncode != 0
        assert "MANGAFLOW_RUNTIME must be gpu or cpu" in result.stderr
        assert not (root / "calls").exists()
    else:
        assert result.returncode == 0, result.stderr
        call = json.loads((root / "calls").read_text().splitlines()[-1])
        assert any(value.endswith("docker-compose.cpu.yml") for value in call) == (expected == "cpu")


@pytest.mark.parametrize("arguments", [["up", "-d"], ["--profile", "example", "up", "-d"], ["restart"]])
def test_launcher_does_not_automatically_emulate_arm(launcher, arguments):
    root, environment = launcher
    (root / ".env").write_text("MANGAFLOW_POSTGRES_PASSWORD=isolated-config-only\nMANGAFLOW_RUNTIME=cpu\n")
    environment["PLATFORM"] = "linux/aarch64"
    result = subprocess.run(["bash", str(root / "scripts/docker.sh"), *arguments], env=environment,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "automatic architecture emulation is unsupported" in result.stderr
    assert len((root / "calls").read_text().splitlines()) == 1


def test_launcher_missing_cpu_files_fails_without_gpu_fallback(launcher):
    root, environment = launcher
    (root / ".env").write_text("MANGAFLOW_POSTGRES_PASSWORD=isolated-config-only\nMANGAFLOW_RUNTIME=cpu\n")
    (root / "docker-compose.cpu.yml").unlink()
    result = subprocess.run(["bash", str(root / "scripts/docker.sh"), "config"], env=environment,
                            capture_output=True, text=True)
    assert result.returncode != 0
    assert "CPU deployment configuration is missing" in result.stderr
    assert not (root / "calls").exists()

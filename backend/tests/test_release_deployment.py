"""Release packaging and promotion failures must not change stable delivery."""
from __future__ import annotations

import importlib
import json
import shutil
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    return importlib.import_module("package_release"), importlib.import_module("release_images")


@pytest.fixture
def public_tree(tmp_path, modules):
    packager, _ = modules
    root = tmp_path / "source"
    for source in (*packager.PUBLIC_FILES.values(), "frontend/package.json"):
        destination = root / source
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / source, destination)
    (root / "VERSION").write_text("0.1.2\n")
    frontend = json.loads((root / "frontend/package.json").read_text())
    frontend["version"] = "0.1.2"
    (root / "frontend/package.json").write_text(json.dumps(frontend))
    (root / ".env").write_text("DO_NOT_PUBLISH=private-secret\n")
    (root / "config.yaml").write_text("DO_NOT_PUBLISH: real-service\n")
    (root / "data").mkdir()
    (root / "data/comic.png").write_bytes(b"private comic")
    return root


def test_bundle_contains_only_public_files_and_can_be_rebuilt_identically(public_tree, tmp_path, modules):
    packager, _ = modules
    archive = packager.package(public_tree, tmp_path / "release")
    first = archive.read_bytes()
    with ZipFile(archive) as source:
        prefix = f"mangaflow-docker-{packager.release_version(public_tree)}/"
        assert set(source.namelist()) == {prefix + name for name in packager.PUBLIC_FILES}
        assert sum(info.file_size for info in source.infolist()) < 100_000
        assert all(b"DO_NOT_PUBLISH" not in source.read(name) for name in source.namelist())
        assert all(b"\r\n" not in source.read(name) for name in source.namelist())
        compose = yaml.safe_load(source.read(prefix + "docker-compose.yml"))
        assert all("build" not in service for service in compose["services"].values())
        for service in ("backend", "frontend"):
            assert compose["services"][service]["image"].endswith("${MANGAFLOW_IMAGE_TAG:-0.1.2}")
        assert b"MANGAFLOW_IMAGE_TAG=0.1.2" in source.read(prefix + ".env.example")
        assert source.getinfo(prefix + "scripts/docker.sh").external_attr >> 16 == 0o100755
    assert packager.package(public_tree, tmp_path / "release").read_bytes() == first


@pytest.mark.parametrize("version", ["v0.1.2", "0.1.3", "0.1.2-beta", "../0.1.2"])
def test_version_mismatch_never_creates_assets(version, public_tree, tmp_path, modules):
    packager, _ = modules
    output = tmp_path / "release"
    with pytest.raises(ValueError, match="does not match"):
        packager.package(public_tree, output, version)
    assert not output.exists()


def test_frontend_version_mismatch_rejects_release(public_tree, tmp_path, modules):
    packager, _ = modules
    (public_tree / "frontend/package.json").write_text(json.dumps({"version": "0.0.0"}))
    with pytest.raises(ValueError, match="Frontend version"):
        packager.package(public_tree, tmp_path / "release")


@pytest.mark.parametrize("broken", ["build", "ocr", "password"])
def test_unsafe_or_inconsistent_templates_reject_packaging(broken, public_tree, tmp_path, modules):
    packager, _ = modules
    if broken == "build":
        path = public_tree / "docker-compose.release.yml"
        path.write_text(path.read_text().replace("  backend:\n", "  backend:\n    build: .\n", 1))
    elif broken == "ocr":
        path = public_tree / "config.example.yaml"
        path.write_text(path.read_text().replace("provider: paddleocr-vl-1.6", "provider: manga-ocr"))
    else:
        path = public_tree / ".env.example"
        path.write_text(path.read_text().replace("MANGAFLOW_POSTGRES_PASSWORD=\n", "MANGAFLOW_POSTGRES_PASSWORD=private\n"))
    with pytest.raises(ValueError):
        packager.package(public_tree, tmp_path / "release")


@pytest.fixture
def staged(tmp_path, modules, public_tree, monkeypatch):
    packager, release = modules
    monkeypatch.setattr(release, "ROOT", public_tree)
    digests = tmp_path / "digests"
    digests.mkdir()
    values = {f"mangaflow-{service}": "sha256:" + character * 64
              for service, character in (("backend", "a"), ("frontend", "b"))}
    for service in ("backend", "frontend"):
        (digests / f"{service}.digest").write_text(values[f"mangaflow-{service}"])
    assets = tmp_path / "assets"
    packager.package(public_tree, assets)
    return release, digests, assets, values


@pytest.mark.parametrize("failure", ["private", "missing_digest", "failed_promotion"])
def test_failure_never_updates_latest_or_publishes_release(failure, staged, monkeypatch):
    release, digests, assets, values = staged
    calls = []
    monkeypatch.setattr(release, "existing_release", lambda tag: None)

    def anonymous(image, reference):
        if failure == "private" and image == "mangaflow-frontend":
            raise RuntimeError("Set BOTH GHCR packages to public")
        return values[image]

    def command(*args):
        calls.append(args)
        if failure == "failed_promotion":
            raise RuntimeError("Registry temporarily unavailable")
        return ""

    monkeypatch.setattr(release, "anonymous_manifest", anonymous)
    monkeypatch.setattr(release, "command", command)
    if failure == "missing_digest":
        (digests / "frontend.digest").unlink()
    with pytest.raises((RuntimeError, FileNotFoundError)):
        release.publish("0.1.2", digests, assets)
    assert not any(":latest" in arg for call in calls for arg in call)
    assert not any(call[:3] == ("gh", "release", "edit") for call in calls)


@pytest.mark.parametrize("latest, promote", [(None, True), ("0.1.0", True), ("0.2.0", False)])
def test_both_images_are_verified_before_promotion_and_older_release_cannot_downgrade_latest(
    latest, promote, staged, monkeypatch,
):
    release, digests, assets, values = staged
    events = []
    monkeypatch.setattr(release, "existing_release", lambda tag: (
        {"tagName": latest, "isDraft": False} if tag is None and latest else None))

    def anonymous(image, reference):
        events.append(("anonymous", image, reference))
        return values[image]

    def command(*args):
        events.append(args)
        return ""

    monkeypatch.setattr(release, "anonymous_manifest", anonymous)
    monkeypatch.setattr(release, "command", command)
    release.publish("0.1.2", digests, assets)
    assert events[0][:2] == ("anonymous", "mangaflow-backend")
    assert events[1][:2] == ("anonymous", "mangaflow-frontend")
    latest_calls = [event for event in events if any(":latest" in arg for arg in event)]
    assert len(latest_calls) == (2 if promote else 0)
    assert events[-1][-1] == f"--latest={str(promote).lower()}"


def test_completed_release_is_idempotent(staged, monkeypatch):
    release, digests, assets, _ = staged
    monkeypatch.setattr(release, "existing_release", lambda tag: {"tagName": "0.1.2", "isDraft": False})
    monkeypatch.setattr(release, "command", lambda *args: pytest.fail("Published release must not change"))
    release.publish("0.1.2", digests, assets)


def test_release_lookup_auth_failure_is_not_treated_as_absent(modules, monkeypatch):
    _, release = modules

    def command(*args):
        raise subprocess.CalledProcessError(1, args, stderr="HTTP 401: Bad credentials")

    monkeypatch.setattr(release, "command", command)
    with pytest.raises(RuntimeError, match="authentication"):
        release.existing_release("0.1.2")


@pytest.fixture
def recovery_run(modules, monkeypatch):
    _, release = modules
    sha = "c" * 40
    run = {"event": "push", "path": ".github/workflows/release.yml", "head_branch": "0.1.2",
           "head_sha": sha, "status": "completed"}
    source = {"sha": sha}
    jobs = {"total_count": 5, "jobs": [
        {"name": name, "status": "completed", "conclusion": "success"}
        for name in ("Validate release", "Release checks / Backend / Python 3.12", "Build backend", "Build frontend")
    ] + [{"name": "Verify and publish", "status": "completed", "conclusion": "failure"}]}
    calls = []

    def command(*args):
        calls.append(args)
        return json.dumps(jobs if "/jobs?" in args[-1] else source if "/commits/" in args[-1] else run)

    monkeypatch.setattr(release, "command", command)
    return release, run, source, jobs, calls


def test_recovery_accepts_original_checks_and_images_without_changing_tag(recovery_run, tmp_path, monkeypatch):
    release, _, source, _, calls = recovery_run
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    release.validate_recovery("0.1.2", "123")
    assert output.read_text() == f"source_sha={source['sha']}\n"
    assert all(call[:2] == ("gh", "api") for call in calls)


@pytest.mark.parametrize("broken", ["tag", "sha", "workflow", "event", "running", "ci", "image", "missing", "pagination"])
def test_recovery_rejects_unrelated_incomplete_or_unverified_builds(broken, recovery_run, tmp_path, monkeypatch):
    release, run, source, jobs, _ = recovery_run
    output = tmp_path / "github-output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    if broken == "tag":
        run["head_branch"] = "0.1.3"
    elif broken == "sha":
        source["sha"] = "d" * 40
    elif broken == "workflow":
        run["path"] = ".github/workflows/ci.yml"
    elif broken == "event":
        run["event"] = "pull_request"
    elif broken == "running":
        run["status"] = "in_progress"
    elif broken == "ci":
        jobs["jobs"][1]["conclusion"] = "failure"
    elif broken == "image":
        jobs["jobs"][2]["conclusion"] = "failure"
    elif broken == "missing":
        jobs["jobs"].pop(3)
    else:
        jobs["total_count"] = 101
    with pytest.raises(ValueError):
        release.validate_recovery("0.1.2", "123")
    assert not output.exists()


@pytest.mark.parametrize("version,run_id", [("../0.1.2", "123"), ("0.1.2", "../123")])
def test_recovery_validates_parameters_before_accessing_github(version, run_id, recovery_run):
    release, _, _, _, calls = recovery_run
    with pytest.raises(ValueError):
        release.validate_recovery(version, run_id)
    assert not calls


@pytest.mark.parametrize("failure", [None, "startup", "cleanup"])
def test_smoke_handles_linux_bind_mount_ownership_and_always_tears_down(failure, public_tree, modules, tmp_path, monkeypatch):
    packager, _ = modules
    smoke = importlib.import_module("smoke_release")
    if smoke.os.name != "posix":
        pytest.skip("Linux bind-mount ownership regression")
    archive = packager.package(public_tree, tmp_path / "assets")
    calls = []
    deployments = []

    def command(args, **kwargs):
        deployment = Path(args[args.index("--project-directory") + 1])
        deployments.append(deployment)
        action = args[args.index(str(deployment / "smoke.override.yml")) + 1:]
        calls.append(action)
        if action[0] == "up":
            # The daemon must never create these host roots as root.
            for folder in ("data", "models"):
                assert (deployment / folder).is_dir()
                assert (deployment / folder).stat().st_uid == smoke.os.getuid()
            if failure == "startup":
                raise subprocess.CalledProcessError(1, args)
        if action[0] == "port":
            return "127.0.0.1:18080\n" if action[1] == "frontend" else "127.0.0.1:18000\n"
        if action[0] == "exec":
            for folder in ("data", "models"):
                assert (deployment / folder / "release-smoke.txt").read_text() == f"persistent-{folder}"
        if action[0] == "run":
            assert action == ["run", "--rm", "--no-deps", "--user", "0:0", "--entrypoint", "chown", "backend",
                              "-R", "--no-dereference", f"{smoke.os.getuid()}:{smoke.os.getgid()}", "/data", "/models"]
            if failure == "cleanup":
                raise subprocess.CalledProcessError(1, args)
        return ""

    def request(url, body=None, headers=None):
        if url.endswith("/health"):
            return {"status": "ok", "version": "0.1.2"}, {}
        if url.endswith("/mangaflow"):
            return {"result": {"tools": list(range(20))}}, {"Mcp-Session-Id": "smoke-session"}
        return {"id": "test-project"}, {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'<div id="root"></div>'

    monkeypatch.setattr(smoke.subprocess, "check_output", command)
    monkeypatch.setattr(smoke, "request", request)
    monkeypatch.setattr(smoke, "urlopen", lambda *args, **kwargs: Response())
    if failure:
        with pytest.raises(subprocess.CalledProcessError):
            smoke.smoke(archive, {"backend": "test-backend", "frontend": "test-frontend"}, anonymous=False)
    else:
        smoke.smoke(archive, {"backend": "test-backend", "frontend": "test-frontend"}, anonymous=False)
    assert [call[0] for call in calls[-3:]] == ["stop", "run", "down"]
    assert calls[-1] == ["down", "--volumes", "--remove-orphans"]
    assert deployments and not deployments[-1].exists()

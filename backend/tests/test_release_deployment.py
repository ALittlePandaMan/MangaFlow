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

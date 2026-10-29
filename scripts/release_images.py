"""Release orchestration. Stage every required image before stable promotion."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from package_release import REGISTRY, ROOT, image_variants, release_version

REPO = "ALittlePandaMan/MangaFlow"
ACCEPT = ", ".join(("application/vnd.oci.image.index.v1+json",
                    "application/vnd.docker.distribution.manifest.list.v2+json",
                    "application/vnd.oci.image.manifest.v1+json",
                    "application/vnd.docker.distribution.manifest.v2+json"))


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True, encoding="utf-8", stderr=subprocess.PIPE).strip()


def existing_release(tag: str | None) -> dict | None:
    args = ["gh", "release", "view", *([tag] if tag else []), "--repo", REPO,
            "--json", "tagName,isDraft,isPrerelease"]
    try:
        return json.loads(command(*args))
    except subprocess.CalledProcessError as exc:
        if "not found" in exc.stderr.lower() or "http 404" in exc.stderr.lower() or "no releases found" in exc.stderr.lower():
            return None
        raise RuntimeError("Cannot inspect GitHub releases; check authentication and permissions") from exc


def anonymous_manifest(image: str, reference: str) -> str:
    """Use a fresh anonymous registry token, never the publisher's Docker login."""
    scope = urlencode({"service": "ghcr.io", "scope": f"repository:alittlepandaman/{image}:pull"})
    try:
        with urlopen(f"https://ghcr.io/token?{scope}", timeout=30) as response:
            token = json.load(response)["token"]
        request = Request(f"https://ghcr.io/v2/alittlepandaman/{image}/manifests/{reference}",
                          headers={"Authorization": f"Bearer {token}", "Accept": ACCEPT})
        with urlopen(request, timeout=30) as response:
            digest = response.headers.get("Docker-Content-Digest", "")
            response.read()
    except HTTPError as exc:
        raise RuntimeError(f"Anonymous pull failed for {image} (HTTP {exc.code}). Set BOTH GHCR packages to public and rerun the release.") from None
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise RuntimeError(f"Registry returned an invalid digest for {image}")
    return digest


def prepare(version: str) -> bool:
    release_version(ROOT, version)
    current = existing_release(version)
    published = bool(current and not current["isDraft"])
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as target:
            target.write(f"published={str(published).lower()}\n")
    print("Release already published; keeping its images and assets unchanged." if published else "Release version validated.")
    return published


def staged_images(digest_dir: Path) -> dict[str, str]:
    images = {}
    for service, (image, _suffix) in image_variants(ROOT).items():
        path = digest_dir / f"{service}.digest"
        if not path.is_file():
            raise ValueError(f"Required staged image artifact is missing: {service}.digest")
        digest = path.read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid {service} image digest")
        if anonymous_manifest(image, digest) != digest:
            raise ValueError(f"Published digest mismatch: {service}")
        images[service] = digest
    return images


def validate_recovery(version: str, run_id: str) -> None:
    """Accept only successful builds of this exact, unchanged release tag."""
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ValueError("Recovery requires a stable release version")
    if not re.fullmatch(r"[1-9]\d*", run_id):
        raise ValueError("Recovery requires a numeric source run ID")
    run = json.loads(command("gh", "api", f"repos/{REPO}/actions/runs/{run_id}"))
    source = json.loads(command("gh", "api", f"repos/{REPO}/commits/{version}"))
    if (run["event"] != "push" or run["path"] != ".github/workflows/release.yml"
            or run["head_branch"] != version or run["head_sha"] != source["sha"]
            or run["status"] != "completed"):
        raise ValueError("Source run must be a completed release build of the unchanged tag")
    jobs = json.loads(command("gh", "api", f"repos/{REPO}/actions/runs/{run_id}/jobs?per_page=100"))
    completed = {job["name"] for job in jobs["jobs"]
                 if job["status"] == "completed" and job["conclusion"] == "success"}
    tree = json.loads(command("gh", "api", f"repos/{REPO}/git/trees/{source['sha']}"))
    required = {"Build backend", "Build frontend", "Validate release"}
    if any(item["path"] == "docker-compose.release.cpu.yml" for item in tree["tree"]):
        required.add("Build backend-cpu")
    prerequisites = [job for job in jobs["jobs"] if job["name"] != "Verify and publish"]
    if (jobs["total_count"] > 100 or not required <= completed
            or not any(job["name"].startswith("Release checks /") for job in prerequisites)
            or any(job["status"] != "completed" or job["conclusion"] != "success" for job in prerequisites)):
        raise ValueError("All original release checks and required image builds must have succeeded")
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a", encoding="utf-8") as target:
            target.write(f"source_sha={source['sha']}\n")
    print(f"Verified release {version} source run {run_id} at {source['sha']}")


def publish(version: str, digest_dir: Path, assets: Path) -> None:
    release_version(ROOT, version)
    if prepare(version):
        return
    images = staged_images(digest_dir)
    archive = assets / f"mangaflow-docker-{version}.zip"
    checksum = archive.with_suffix(".zip.sha256")
    if not archive.is_file() or not checksum.is_file():
        raise ValueError("Deployment ZIP and checksum must be built before promotion")
    latest = existing_release(None)
    latest_version = latest["tagName"] if latest else "0.0.0"
    if not re.fullmatch(r"\d+\.\d+\.\d+", latest_version):
        raise ValueError("Latest release tag is not a supported stable version; inspect before promotion")
    is_latest = tuple(map(int, version.split("."))) >= tuple(map(int, latest_version.split(".")))
    notes = ROOT / "docs" / "releases" / f"{version}.md"
    variants = image_variants(ROOT)
    for service, digest in images.items():
        image, suffix = variants[service]
        tag = version + suffix
        command("docker", "buildx", "imagetools", "create", "--prefer-index=false", "--tag", f"{REGISTRY}/{image}:{tag}",
                f"{REGISTRY}/{image}@{digest}")
        if anonymous_manifest(image, tag) != digest:
            raise RuntimeError(f"Version tag did not retain the tested image: {image}")
    if existing_release(version) is None:
        command("gh", "release", "create", version, "--repo", REPO, "--verify-tag", "--draft",
                "--title", version, *(["--notes-file", str(notes)] if notes.is_file() else ["--generate-notes"]))
    command("gh", "release", "upload", version, str(archive), str(checksum), "--repo", REPO, "--clobber")
    if is_latest:
        for service, digest in images.items():
            image, suffix = variants[service]
            command("docker", "buildx", "imagetools", "create", "--prefer-index=false", "--tag", f"{REGISTRY}/{image}:latest{suffix}",
                    f"{REGISTRY}/{image}@{digest}")
    command("gh", "release", "edit", version, "--repo", REPO, "--draft=false",
            *(["--notes-file", str(notes)] if notes.is_file() else []), f"--latest={str(is_latest).lower()}")
    print(f"Published {version}; stable latest updated: {is_latest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "publish", "validate-recovery"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-run-id")
    parser.add_argument("--digest-dir", type=Path, default=ROOT / "dist/digests")
    parser.add_argument("--assets", type=Path, default=ROOT / "dist/release")
    args = parser.parse_args()
    if args.action == "validate-recovery":
        if not args.source_run_id:
            parser.error("--source-run-id is required for recovery")
        validate_recovery(args.version, args.source_run_id)
    elif args.action == "prepare":
        prepare(args.version)
    elif args.action == "verify":
        release_version(ROOT, args.version)
        staged_images(args.digest_dir)
        print("All required staged images are anonymously accessible.")
    else:
        publish(args.version, args.digest_dir, args.assets)


if __name__ == "__main__":
    main()

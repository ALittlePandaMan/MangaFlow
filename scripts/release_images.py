"""Release orchestration. Build jobs stage both images before stable promotion."""
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

from package_release import REGISTRY, ROOT, release_version

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
    for service in ("backend", "frontend"):
        digest = (digest_dir / f"{service}.digest").read_text(encoding="utf-8").strip()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            raise ValueError(f"Invalid {service} image digest")
        image = f"mangaflow-{service}"
        if anonymous_manifest(image, digest) != digest:
            raise ValueError(f"Published digest mismatch: {service}")
        images[image] = digest
    return images


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
    for image, digest in images.items():
        command("docker", "buildx", "imagetools", "create", "--prefer-index=false", "--tag", f"{REGISTRY}/{image}:{version}",
                f"{REGISTRY}/{image}@{digest}")
        if anonymous_manifest(image, version) != digest:
            raise RuntimeError(f"Version tag did not retain the tested image: {image}")
    if existing_release(version) is None:
        command("gh", "release", "create", version, "--repo", REPO, "--verify-tag", "--draft",
                "--title", version, "--generate-notes")
    command("gh", "release", "upload", version, str(archive), str(checksum), "--repo", REPO, "--clobber")
    if is_latest:
        for image, digest in images.items():
            command("docker", "buildx", "imagetools", "create", "--prefer-index=false", "--tag", f"{REGISTRY}/{image}:latest",
                    f"{REGISTRY}/{image}@{digest}")
    command("gh", "release", "edit", version, "--repo", REPO, "--draft=false", f"--latest={str(is_latest).lower()}")
    print(f"Published {version}; stable latest updated: {is_latest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify", "publish"))
    parser.add_argument("--version", required=True)
    parser.add_argument("--digest-dir", type=Path, default=ROOT / "dist/digests")
    parser.add_argument("--assets", type=Path, default=ROOT / "dist/release")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare(args.version)
    elif args.action == "verify":
        release_version(ROOT, args.version)
        staged_images(args.digest_dir)
        print("Both staged images are anonymously accessible.")
    else:
        publish(args.version, args.digest_dir, args.assets)


if __name__ == "__main__":
    main()

"""Build a small, reproducible deployment ZIP from public files only."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/ALittlePandaMan/MangaFlow"
REGISTRY = "ghcr.io/alittlepandaman"
PUBLIC_FILES = {
    "docker-compose.yml": "docker-compose.release.yml",
    "docker-compose.cpu.yml": "docker-compose.release.cpu.yml",
    ".env.example": ".env.example",
    "config.example.yaml": "config.example.yaml",
    "scripts/docker.sh": "scripts/docker.sh",
    "scripts/prepare-config.sh": "scripts/prepare-config.sh",
    "README.md": "docker/README.deploy.md",
    "docs/agent-deployment.md": "docs/agent-deployment.md",
    "VERSION": "VERSION",
    "LICENSE": "LICENSE",
}


def image_variants(root: Path) -> dict[str, tuple[str, str]]:
    """The source tree determines whether this release includes a CPU build."""
    variants = {"backend": ("mangaflow-backend", ""), "frontend": ("mangaflow-frontend", "")}
    if (root / "docker-compose.release.cpu.yml").is_file():
        variants["backend-cpu"] = ("mangaflow-backend", "-cpu")
    return variants


def release_version(root: Path, requested: str | None = None) -> str:
    version = (root / "VERSION").read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", version):
        raise ValueError("VERSION must use the stable semantic version format 0.1.2")
    if requested is not None and requested != version:
        raise ValueError(f"Release tag {requested!r} does not match VERSION {version!r}")
    frontend = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    if frontend["version"] != version:
        raise ValueError("Frontend version does not match VERSION")
    return version


def public_files(root: Path, version: str) -> dict[str, bytes]:
    files = {}
    for name, source in PUBLIC_FILES.items():
        path = root / source
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing or linked public deployment file: {source}")
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        if name in {"docker-compose.yml", "docker-compose.cpu.yml"}:
            text = re.sub(r"\$\{MANGAFLOW_IMAGE_TAG:-[^}]+\}", "${MANGAFLOW_IMAGE_TAG:-" + version + "}", text)
            compose = yaml.safe_load(text.replace("${MANGAFLOW_IMAGE_TAG:-" + version + "}", version).replace("!reset []", "[]"))
            services = compose["services"]
            if any("build" in item for item in services.values()):
                raise ValueError("Release Compose must not contain build instructions")
            for service in (("backend", "frontend") if name == "docker-compose.yml" else ("backend",)):
                suffix = "-cpu" if name == "docker-compose.cpu.yml" else ""
                if services[service]["image"] != f"{REGISTRY}/mangaflow-{service}:{version}{suffix}":
                    raise ValueError("Release image names and versions must match")
            if name == "docker-compose.cpu.yml" and (services["backend"].get("gpus") != [] or
                    services["backend"]["environment"].get("MANGAFLOW_RUNTIME") != "cpu" or
                    services["backend"]["environment"].get("CUDA_VISIBLE_DEVICES") != "" or
                    services["backend"]["environment"].get("MANGAFLOW_SEGMENTATION_BACKEND") != "opencv"):
                raise ValueError("CPU deployment must disable GPU requests and CUDA inference")
        if name == ".env.example":
            # The generated Compose pins its defaults; this override is the
            # explicit upgrade control and is never copied over an existing .env.
            text = text.rstrip() + f"\n\n# Set both application images to the same release when upgrading.\nMANGAFLOW_IMAGE_TAG={version}\n"
            if re.search(r"^MANGAFLOW_POSTGRES_PASSWORD=.+", text, re.MULTILINE):
                raise ValueError("The public template must not contain a database password")
        if name == "README.md":
            text = text.replace("{{VERSION}}", version).replace("(../docs/agent-deployment.md)", "(docs/agent-deployment.md)")
        files[name] = text.encode("utf-8")
    ocr = yaml.safe_load(files["config.example.yaml"])["stages"]["ocr"]
    if ocr["provider"] != "paddleocr-vl-1.6" or "model" in ocr.get("config", {}):
        raise ValueError("The deployment default must use PaddleOCR-VL 1.6 without MangaOCR options")
    return files


def package(root: Path, output: Path, requested: str | None = None) -> Path:
    version = release_version(root, requested)
    files = public_files(root, version)
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"mangaflow-docker-{version}.zip"
    prefix = f"mangaflow-docker-{version}/"
    with ZipFile(archive, "w", compression=ZIP_DEFLATED, compresslevel=9) as target:
        for name, content in sorted(files.items()):
            entry = ZipInfo(prefix + name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = (0o100755 if name.endswith(".sh") else 0o100644) << 16
            entry.compress_type = ZIP_DEFLATED
            target.writestr(entry, content)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
    return archive


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", help="Stable tag; must equal VERSION and frontend/package.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist/release")
    args = parser.parse_args()
    print(package(ROOT, args.output_dir, args.version))


if __name__ == "__main__":
    main()

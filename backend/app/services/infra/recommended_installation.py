"""Fixed local setup. Status checks are offline; only confirmed jobs download assets."""
from __future__ import annotations

import hashlib
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from threading import RLock, Thread
from urllib.parse import urlsplit
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import ModelConfig
from app.services.detection.classification import MODEL_RELATIVE_PATH, MODEL_SHA256, classifier_python
from app.services.detection.install_classification import install_weights
from app.services.infra.model_manifest import persist_model_settings
from app.services.infra.model_provisioning import _has_rendering_font, ensure_selected_config
from app.services.ocr.local_catalog import local_ocr_python, resolve_assets
from app.services.registry import registry
from sqlalchemy import select
from sqlalchemy.orm import Session

LOCAL_SETUP = (
    ("detection", "rfdetr", "RF-DETR"),
    ("ocr", "paddleocr-vl-1.6", "PaddleOCR-VL 1.6"),
    ("inpainting", "lama", "LaMa"),
    ("rendering", "pillow", "Pillow"),
)


@lru_cache(maxsize=8)
def _verified_detection_file(path: str, size: int, modified: int) -> bool:
    # Cache by file identity, not just pathname; replacing/truncating weights
    # invalidates the result without hashing a large file on every status poll.
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest() == MODEL_SHA256


def _lama_path() -> Path:
    if os.environ.get("LAMA_MODEL"):
        return Path(os.environ["LAMA_MODEL"])
    from simple_lama_inpainting.models.model import LAMA_MODEL_URL
    from torch.hub import get_dir

    return Path(get_dir()) / "checkpoints" / Path(urlsplit(LAMA_MODEL_URL).path).name


def _valid_lama_file(path: Path) -> bool:
    # torch.jit.load uses a TorchScript ZIP archive. Reject partial downloads
    # without loading the model onto a GPU or touching the inference queue.
    try:
        with ZipFile(path) as archive:
            names = archive.namelist()
            return any(name.endswith("/constants.pkl") for name in names) and any(
                name.endswith("/data.pkl") for name in names
            )
    except (OSError, BadZipFile):
        return False


def _dependency_ready(kind: str, provider: str) -> bool:
    if provider == "rfdetr":
        return classifier_python({}).is_file()
    if provider == "paddleocr-vl-1.6":
        return local_ocr_python().is_file()
    return registry.is_installed(kind, provider)


def _assets_ready(provider: str) -> bool:
    if provider == "rfdetr":
        path = get_settings().model_dir / MODEL_RELATIVE_PATH
        try:
            stat = path.stat()
            return _verified_detection_file(str(path), stat.st_size, stat.st_mtime_ns)
        except OSError:
            return False
    if provider == "paddleocr-vl-1.6":
        try:
            directory, _ = resolve_assets(provider, download=False)
            # The catalog checks every required file, including processor and
            # tokenizer assets; an empty weight file is not an installed model.
            return (directory / "model.safetensors").stat().st_size > 0
        except (OSError, ValueError):
            return False
    if provider == "lama":
        return _valid_lama_file(_lama_path())
    return _has_rendering_font()


def _local_status() -> list[dict]:
    models = []
    for kind, provider, name in LOCAL_SETUP:
        dependency = _dependency_ready(kind, provider)
        try:
            installed = dependency and _assets_ready(provider)
        except (ImportError, OSError, ValueError):
            installed = False
        models.append({
            "kind": kind, "provider": provider, "name": name,
            "installed": installed, "status": "ready" if installed else "missing",
            "error": None if dependency else f"{name} 的运行依赖未安装，请先安装对应运行环境。",
        })
    return models


def _download_lama() -> None:
    from simple_lama_inpainting.models.model import LAMA_MODEL_URL
    from torch.hub import download_url_to_file

    destination = _lama_path()
    if _valid_lama_file(destination):
        return
    # A custom checkpoint path is user-owned; never overwrite it with a
    # different model just because the configured file is missing/corrupt.
    if os.environ.get("LAMA_MODEL"):
        raise ValueError("LAMA_MODEL 指定的模型文件无效，请检查该自定义路径。")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}-{uuid4().hex}.tmp")
    try:
        download_url_to_file(LAMA_MODEL_URL, str(temporary), progress=False)
        if not _valid_lama_file(temporary):
            raise ValueError("LaMa 模型文件校验失败，请重试。")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _download(provider: str) -> None:
    if provider == "rfdetr":
        install_weights()
    elif provider == "paddleocr-vl-1.6":
        resolve_assets(provider, download=True)
    elif provider == "lama":
        _download_lama()
    else:
        raise ValueError("没有可用于 Pillow 排版的字体，请先安装字体。")


def _configured(db: Session) -> bool:
    selected = set(db.execute(select(ModelConfig.kind, ModelConfig.provider).where(
        ModelConfig.enabled.is_(True), ModelConfig.is_default.is_(True),
    )).all())
    return all((kind, provider) in selected for kind, provider, _ in LOCAL_SETUP)


class RecommendedInstallation:
    def __init__(self) -> None:
        self._lock = RLock()
        self._state = "idle"
        self._models: list[dict] = []
        self._error: str | None = None

    def status(self, db: Session) -> dict:
        with self._lock:
            state, error = self._state, self._error
            models = deepcopy(self._models) if state == "installing" else None
        models = models if models is not None else _local_status()
        return {
            "state": state, "installed": all(item["installed"] for item in models),
            "configured": _configured(db), "models": models, "error": error,
        }

    def start(self, db: Session) -> dict:
        with self._lock:
            if self._state != "installing":
                self._models = _local_status()
                self._error = None
                self._state = "installing"
                try:
                    Thread(target=self._run, name="recommended-setup", daemon=True).start()
                except Exception:
                    self._state = "error"
                    self._error = "无法启动安装任务，请重试。"
                    raise
        return self.status(db)

    def _run(self) -> None:
        try:
            for index, (kind, provider, name) in enumerate(LOCAL_SETUP):
                with self._lock:
                    entry = deepcopy(self._models[index])
                if not _dependency_ready(kind, provider):
                    raise ValueError(entry["error"] or f"{name} 的运行依赖未安装。")
                if not entry["installed"]:
                    with self._lock:
                        self._models[index]["status"] = "downloading"
                    _download(provider)
                if not _assets_ready(provider):
                    raise ValueError(f"{name} 模型文件不完整，请重试。")
                with self._lock:
                    self._models[index].update(installed=True, status="ready", error=None)
            # Downloads never hold a database transaction or GPU/model slot.
            # Activate defaults only after all four resources are complete.
            with SessionLocal() as db:
                for kind, provider, _ in LOCAL_SETUP:
                    ensure_selected_config(db, kind=kind, provider=provider)
                db.commit()
                settings = get_settings()
                persist_model_settings(db, settings.model_manifest_path, environment_path=settings.environment_file_path)
            with self._lock:
                self._state = "complete"
        except Exception as exc:
            with self._lock:
                self._state = "error"
                self._error = str(exc) or "推荐配置安装失败，请重试。"


recommended_installation = RecommendedInstallation()

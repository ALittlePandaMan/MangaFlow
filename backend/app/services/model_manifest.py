from __future__ import annotations

import errno
import os
import re
import uuid
from pathlib import Path
from typing import Any, Literal

import yaml
from app.core.secrets import get_secret_store
from app.models import ModelConfig
from app.services.device import paddle_cuda_available, torch_cuda_available
from app.services.registry import registry
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import select, update
from sqlalchemy.orm import Session

MODEL_STAGES = ("detection", "inpainting", "ocr", "rendering", "translation")
MANIFEST_STAGES = (*MODEL_STAGES, "font")
FONT_PROVIDERS = {"builtin", "builtin-noto-cjk"}
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SENSITIVE_CONFIG_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "token",
    "access_token",
}


class ModelManifestError(ValueError):
    """Raised when a portable model manifest is invalid."""


class ManifestStage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    name: str | None = Field(default=None, min_length=1, max_length=100)
    device: Literal["recommended", "cpu", "cuda:0"] | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    api_key_env: str | None = None

    @field_validator("provider", "name", mode="after")
    @classmethod
    def strip_names(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("api_key_env")
    @classmethod
    def validate_environment_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not _ENVIRONMENT_NAME.fullmatch(normalized):
            raise ValueError("api_key_env must be a valid environment variable name")
        return normalized


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    apply: Literal["fill-missing"] = "fill-missing"
    preload: bool = True
    stages: dict[str, ManifestStage]

    @model_validator(mode="after")
    def validate_stages(self) -> ModelManifest:
        actual = set(self.stages)
        expected = set(MANIFEST_STAGES)
        if actual != expected:
            missing = ", ".join(sorted(expected - actual))
            extra = ", ".join(sorted(actual - expected))
            details = []
            if missing:
                details.append(f"missing stages: {missing}")
            if extra:
                details.append(f"unknown stages: {extra}")
            raise ValueError("manifest must define exactly six stages (" + "; ".join(details) + ")")
        for kind, stage in self.stages.items():
            if stage.api_key_env is not None and kind != "translation":
                raise ValueError(f"{kind}.api_key_env is only supported for translation")
            if stage.device is not None and kind in {"translation", "font"}:
                raise ValueError(f"{kind}.device is not supported")
        return self


def load_model_manifest(path: str | Path) -> ModelManifest:
    """Load and completely validate a versioned model manifest."""
    manifest_path = Path(path)
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ModelManifestError(f"Model manifest does not exist: {manifest_path}") from exc
    except OSError as exc:
        raise ModelManifestError(f"Unable to read model manifest {manifest_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        location = getattr(exc, "problem_mark", None)
        suffix = f" at line {location.line + 1}, column {location.column + 1}" if location else ""
        raise ModelManifestError(f"Model manifest is not valid YAML{suffix}") from exc
    try:
        manifest = ModelManifest.model_validate(raw)
    except ValidationError as exc:
        raise ModelManifestError(f"Invalid model manifest: {exc}") from exc
    _resolved_manifest_configs(manifest)
    return manifest


def persist_model_settings(
    db: Session,
    path: str | Path | None,
    *,
    environment_path: str | Path | None = None,
) -> ModelManifest | None:
    """Persist active settings without ever writing secrets into YAML.

    The five active pipeline configurations are serialized from the database.
    The font stage remains a portable resource declaration, while a translation
    API key is written only to the ignored environment file referenced by YAML.
    """

    if path is None:
        return None
    manifest_path = Path(path)
    existing: ModelManifest | None = None
    if manifest_path.is_file():
        existing = load_model_manifest(manifest_path)

    stages: dict[str, ManifestStage] = {}
    defaults: dict[str, ModelConfig] = {}
    descriptors = {(item["kind"], item["name"]): item for item in registry.describe()}
    for kind in MODEL_STAGES:
        item = db.scalar(
            select(ModelConfig)
            .where(
                ModelConfig.kind == kind,
                ModelConfig.enabled.is_(True),
                ModelConfig.is_default.is_(True),
            )
            .order_by(ModelConfig.updated_at.desc())
        )
        if item is None:
            if existing is None:
                raise ModelManifestError(f"Cannot persist config.yaml without an active {kind!r} configuration")
            stages[kind] = existing.stages[kind].model_copy(deep=True)
            continue
        defaults[kind] = item
        config = _without_sensitive_values(dict(item.config or {}))
        descriptor = descriptors.get((kind, item.provider), {})
        devices = {str(device).lower() for device in descriptor.get("devices", [])}
        device: Literal["recommended", "cpu", "cuda:0"] | None = None
        if devices != {"remote"}:
            device = _portable_device(config.pop("device", None), devices)
        stages[kind] = ManifestStage(
            provider=item.provider,
            name=item.name,
            device=device,
            config=config,
            api_key_env="MANGAFLOW_TRANSLATION_API_KEY" if kind == "translation" else None,
        )

    stages["font"] = (
        existing.stages["font"].model_copy(deep=True)
        if existing is not None
        else ManifestStage(
            provider="builtin-noto-cjk",
            name="Noto Sans CJK",
            config={"package": "fonts-noto-cjk"},
        )
    )
    manifest = ModelManifest(
        schema_version=1,
        apply="fill-missing",
        preload=existing.preload if existing is not None else True,
        stages=stages,
    )
    _resolved_manifest_configs(manifest)
    _atomic_write(
        manifest_path,
        yaml.safe_dump(
            manifest.model_dump(mode="python", exclude_none=True),
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=120,
        ),
        mode=0o644,
    )

    if environment_path is not None:
        translation = defaults.get("translation")
        secret = (
            get_secret_store().decrypt(translation.encrypted_api_key)
            if translation is not None and translation.encrypted_api_key
            else ""
        )
        _persist_environment_value(
            Path(environment_path),
            "MANGAFLOW_TRANSLATION_API_KEY",
            secret or "",
        )
    return manifest


def apply_model_manifest(db: Session, manifest: ModelManifest | dict[str, Any]) -> list[dict[str, Any]]:
    """Fill missing defaults from a manifest without overwriting active UI configuration."""
    normalized = _coerce_manifest(manifest)
    resolved = _resolved_manifest_configs(normalized)
    report: list[dict[str, Any]] = []
    try:
        for kind in MODEL_STAGES:
            stage = normalized.stages[kind]
            current = db.scalar(
                select(ModelConfig)
                .where(
                    ModelConfig.kind == kind,
                    ModelConfig.enabled.is_(True),
                    ModelConfig.is_default.is_(True),
                )
                .order_by(ModelConfig.updated_at.desc())
            )
            if current is not None:
                secret_updated = current.provider == stage.provider and _apply_api_key_from_environment(current, stage)
                report.append(
                    _apply_report(
                        current,
                        action="updated" if secret_updated else "kept",
                    )
                )
                continue

            existing = db.scalar(
                select(ModelConfig)
                .where(ModelConfig.kind == kind, ModelConfig.provider == stage.provider)
                .order_by(ModelConfig.updated_at.desc())
            )
            db.execute(
                update(ModelConfig)
                .where(ModelConfig.kind == kind, ModelConfig.is_default.is_(True))
                .values(is_default=False)
            )
            if existing is None:
                existing = ModelConfig(
                    kind=kind,
                    name=_available_name(db, kind, stage.name or f"{kind}:{stage.provider}"),
                    provider=stage.provider,
                    enabled=True,
                    is_default=True,
                    config=dict(resolved[kind]),
                )
                db.add(existing)
                action = "created"
            else:
                merged_config = dict(existing.config or {})
                merged_config.update(resolved[kind])
                existing.config = merged_config
                existing.enabled = True
                existing.is_default = True
                action = "updated"
            _apply_api_key_from_environment(existing, stage)
            db.flush()
            report.append(_apply_report(existing, action=action))

        font_stage = normalized.stages["font"]
        report.append(
            {
                "kind": "font",
                "provider": font_stage.provider,
                "action": "kept",
                "has_api_key": False,
            }
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return report


def preload_manifest_models(db: Session, manifest: ModelManifest | dict[str, Any]) -> list[dict[str, Any]]:
    """Load/download each active local provider and report failures independently."""
    normalized = _coerce_manifest(manifest)
    _resolved_manifest_configs(normalized)
    report: list[dict[str, Any]] = []
    for kind in MANIFEST_STAGES:
        stage = normalized.stages[kind]
        if kind in {"translation", "font"}:
            report.append(_preload_report(kind, stage.provider, "skipped", None))
            continue
        if not normalized.preload:
            report.append(_preload_report(kind, stage.provider, "skipped", "preload disabled by manifest"))
            continue
        item = db.scalar(
            select(ModelConfig)
            .where(
                ModelConfig.kind == kind,
                ModelConfig.enabled.is_(True),
                ModelConfig.is_default.is_(True),
            )
            .order_by(ModelConfig.updated_at.desc())
        )
        if item is None:
            report.append(_preload_report(kind, stage.provider, "missing", "no active default configuration"))
            continue
        if not registry.is_installed(kind, item.provider):
            report.append(
                _preload_report(kind, item.provider, "dependency_missing", "provider dependency is not installed")
            )
            continue
        try:
            registry.create(kind, item.provider, dict(item.config or {})).ensure_loaded()
        except Exception as exc:
            report.append(_preload_report(kind, item.provider, "error", str(exc)))
        else:
            report.append(_preload_report(kind, item.provider, "ready", None))
    return report


def _coerce_manifest(manifest: ModelManifest | dict[str, Any]) -> ModelManifest:
    if isinstance(manifest, ModelManifest):
        return manifest
    try:
        return ModelManifest.model_validate(manifest)
    except ValidationError as exc:
        raise ModelManifestError(f"Invalid model manifest: {exc}") from exc


def _without_sensitive_values(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _without_sensitive_values(item)
            for key, item in value.items()
            if str(key).strip().lower().replace("-", "_") not in _SENSITIVE_CONFIG_KEYS
        }
    if isinstance(value, list):
        return [_without_sensitive_values(item) for item in value]
    return value


def _portable_device(value: Any, devices: set[str]) -> Literal["recommended", "cpu", "cuda:0"]:
    normalized = str(value or "").strip().lower()
    if normalized in {"cuda", "gpu", "gpu:0", "cuda:0"}:
        return "cuda:0"
    if normalized == "recommended":
        return "recommended"
    if normalized == "cpu":
        return "cpu"
    return "cpu" if "cpu" in devices else "recommended"


def _persist_environment_value(path: Path, key: str, value: str) -> None:
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    replacement = f'{key}="{escaped}"'
    pattern = re.compile(rf"^(?:export\s+)?{re.escape(key)}\s*=.*$", re.MULTILINE)
    if pattern.search(existing):
        updated = pattern.sub(replacement, existing, count=1)
    else:
        separator = "" if not existing or existing.endswith("\n") else "\n"
        updated = f"{existing}{separator}{replacement}\n"
    _atomic_write(path, updated, mode=0o600)


def _atomic_write(path: Path, content: str, *, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.chmod(mode)
        try:
            os.replace(temporary, path)
        except OSError as exc:
            # A single-file Docker bind mount is a mount point and cannot be
            # replaced. It remains writable, so fall back to a flushed
            # in-place update while keeping atomic replacement everywhere else.
            if exc.errno not in {errno.EBUSY, errno.EXDEV}:
                raise
            with path.open("w", encoding="utf-8") as destination:
                destination.write(content)
                destination.flush()
                os.fsync(destination.fileno())
            path.chmod(mode)
    finally:
        temporary.unlink(missing_ok=True)


def _resolved_manifest_configs(manifest: ModelManifest) -> dict[str, dict[str, Any]]:
    descriptors = {(item["kind"], item["name"]): item for item in registry.describe()}
    resolved: dict[str, dict[str, Any]] = {}
    for kind in MODEL_STAGES:
        stage = manifest.stages[kind]
        descriptor = descriptors.get((kind, stage.provider))
        if descriptor is None:
            raise ModelManifestError(f"Provider {stage.provider!r} is not registered for stage {kind!r}")
        resolved[kind] = _resolved_stage_config(kind, stage, descriptor)
    font_provider = manifest.stages["font"].provider
    if font_provider not in FONT_PROVIDERS:
        raise ModelManifestError(
            f"Provider {font_provider!r} is not registered for stage 'font'; expected one of {sorted(FONT_PROVIDERS)}"
        )
    return resolved


def _resolved_stage_config(kind: str, stage: ManifestStage, descriptor: dict[str, Any]) -> dict[str, Any]:
    config = dict(stage.config)
    nested_device = config.pop("device", None)
    if nested_device is not None and stage.device is not None and nested_device != stage.device:
        raise ModelManifestError(f"Conflicting device values for stage {kind!r}")
    requested = stage.device or nested_device
    devices = {str(device).lower() for device in descriptor.get("devices", [])}
    if devices == {"remote"}:
        if requested is not None:
            raise ModelManifestError(f"Stage {kind!r} uses a remote provider and cannot declare a device")
        return config
    if requested is None:
        requested = "recommended" if "cuda" in devices else "cpu"
    if requested not in {"recommended", "cpu", "cuda:0"}:
        raise ModelManifestError(
            f"Unsupported device {requested!r} for stage {kind!r}; use recommended, cpu, or cuda:0"
        )
    if requested == "recommended":
        if "cuda" not in devices:
            resolved_device = "cpu"
        elif stage.provider == "paddleocr":
            resolved_device = "cuda:0" if paddle_cuda_available() else "cpu"
        else:
            resolved_device = "cuda:0" if torch_cuda_available() else "cpu"
    else:
        resolved_device = requested
    capability = "cuda" if resolved_device.startswith("cuda") else resolved_device
    if capability not in devices:
        raise ModelManifestError(
            f"Provider {stage.provider!r} for stage {kind!r} does not support device {resolved_device!r}"
        )
    config["device"] = resolved_device
    return config


def _apply_api_key_from_environment(item: ModelConfig, stage: ManifestStage) -> bool:
    if not stage.api_key_env:
        return False
    value = os.environ.get(stage.api_key_env)
    if not value:
        return False
    secret_store = get_secret_store()
    if item.encrypted_api_key:
        try:
            if secret_store.decrypt(item.encrypted_api_key) == value:
                return False
        except ValueError:
            # A deliberately supplied environment secret is authoritative when
            # the old database was copied without its original Fernet key.
            pass
    item.encrypted_api_key = secret_store.encrypt(value)
    return True


def _available_name(db: Session, kind: str, requested: str) -> str:
    base = requested.strip()[:100] or f"{kind}:manifest"
    candidate = base
    suffix = 2
    while db.scalar(select(ModelConfig.id).where(ModelConfig.kind == kind, ModelConfig.name == candidate)):
        marker = f" ({suffix})"
        candidate = f"{base[: 100 - len(marker)]}{marker}"
        suffix += 1
    return candidate


def _apply_report(item: ModelConfig, *, action: str) -> dict[str, Any]:
    return {
        "id": item.id,
        "kind": item.kind,
        "provider": item.provider,
        "action": action,
        "has_api_key": bool(item.encrypted_api_key),
    }


def _preload_report(kind: str, provider: str, status: str, error: str | None) -> dict[str, Any]:
    return {"kind": kind, "provider": provider, "status": status, "error": error}


__all__ = [
    "MANIFEST_STAGES",
    "MODEL_STAGES",
    "ManifestStage",
    "ModelManifest",
    "ModelManifestError",
    "apply_model_manifest",
    "load_model_manifest",
    "persist_model_settings",
    "preload_manifest_models",
]

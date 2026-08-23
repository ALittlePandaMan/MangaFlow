from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.database import SessionLocal, initialize_database
from app.core.secrets import get_secret_store
from app.models import ModelConfig
from app.services.device import paddle_cuda_available, torch_cuda_available
from app.services.registry import registry
from app.services.translation.prompts import DEFAULT_TRANSLATION_PROMPT
from sqlalchemy import select, update
from sqlalchemy.orm import Session


@dataclass(frozen=True, slots=True)
class RecommendedModel:
    kind: str
    name: str
    provider: str
    config: dict[str, Any]


RECOMMENDED_MODELS = {
    "detection": RecommendedModel(
        "detection",
        "推荐文字检测（PaddleOCR）",
        "paddleocr",
        {
            "device": "cuda:0",
            "language": "japan",
            "ocr_version": "PP-OCRv5",
            "box_threshold": 0.45,
            "unclip_ratio": 1.8,
            "group_text_lines": False,
        },
    ),
    "ocr": RecommendedModel(
        "ocr",
        "推荐漫画识别（MangaOCR）",
        "manga-ocr",
        {"device": "cuda:0", "model": "kha-white/manga-ocr-base"},
    ),
    "translation": RecommendedModel(
        "translation", "安全翻译占位（需配置真实模型）", "passthrough", {"review_required": True}
    ),
    "inpainting": RecommendedModel(
        "inpainting", "推荐复杂修复（LaMa）", "lama", {"device": "cuda:0"}
    ),
    "rendering": RecommendedModel("rendering", "默认排版渲染（Pillow）", "pillow", {"min_font_size": 10}),
}

SELECTABLE_MODELS: dict[tuple[str, str], RecommendedModel] = {
    (item.kind, item.provider): item for item in RECOMMENDED_MODELS.values()
}
SELECTABLE_MODELS.update(
    {
        ("detection", "opencv-fallback"): RecommendedModel(
            "detection", "本地 OpenCV 文本检测", "opencv-fallback", {"device": "cpu"}
        ),
        ("ocr", "paddleocr"): RecommendedModel(
            "ocr", "PaddleOCR 文字识别", "paddleocr", {"device": "cuda:0", "language": "japan"}
        ),
        ("ocr", "tesseract"): RecommendedModel(
            "ocr", "Tesseract OCR", "tesseract", {"device": "cpu"}
        ),
        ("ocr", "review-fallback"): RecommendedModel(
            "ocr", "人工审核占位 OCR", "review-fallback", {}
        ),
        ("inpainting", "hybrid"): RecommendedModel(
            "inpainting", "LaMa 兼容修复", "hybrid", {"device": "cuda:0"}
        ),
        ("inpainting", "opencv"): RecommendedModel(
            "inpainting", "OpenCV 快速修复", "opencv", {"device": "cpu"}
        ),
    }
)

ADDITIONAL_RECOMMENDED_MODELS = (
    RecommendedModel(
        "translation",
        "自定义云端翻译",
        "openai-compatible",
        {
            "api_protocol": "auto",
            "timeout": 90,
            "retries": 2,
            "temperature": 0.2,
            "prompt": DEFAULT_TRANSLATION_PROMPT,
        },
    ),
)
for _additional_model in ADDITIONAL_RECOMMENDED_MODELS:
    SELECTABLE_MODELS[(_additional_model.kind, _additional_model.provider)] = _additional_model

LEGACY_DEEPSEEK_TEMPLATE_NAME = "推荐云端翻译（DeepSeek）"
LEGACY_DEEPSEEK_TEMPLATE_CONFIG = {
    "base_url": "https://api.deepseek.com",
    "model": "deepseek-v4-flash",
    "timeout": 90,
    "retries": 2,
    "temperature": 0.2,
    "prompt": "Translate manga dialogue naturally and preserve character voice. Return only valid JSON.",
}
LEGACY_CUSTOM_TRANSLATION_TEMPLATE_CONFIG = {
    "timeout": 90,
    "retries": 2,
    "temperature": 0.2,
    "prompt": "Translate manga dialogue naturally and preserve character voice. Return only valid JSON.",
}


def _recommendation(kind: str) -> RecommendedModel:
    return RECOMMENDED_MODELS[kind]


def _is_fallback(item: ModelConfig) -> bool:
    descriptor = next(
        (
            entry
            for entry in registry.describe()
            if entry["kind"] == item.kind and entry["name"] == item.provider
        ),
        {},
    )
    return bool(descriptor.get("is_fallback"))


def ensure_recommended_config(
    db: Session,
    kind: str,
    *,
    upgrade_fallbacks: bool = False,
) -> tuple[ModelConfig, str]:
    """Ensure one usable default config exists for a pipeline stage."""
    recommendation = _recommendation(kind)
    current = db.scalar(
        select(ModelConfig)
        .where(ModelConfig.kind == kind, ModelConfig.enabled.is_(True), ModelConfig.is_default.is_(True))
        .order_by(ModelConfig.updated_at.desc())
    )
    if current is not None and not (upgrade_fallbacks and _is_fallback(current)):
        return current, "kept"

    existing = db.scalar(
        select(ModelConfig).where(ModelConfig.kind == kind, ModelConfig.name == recommendation.name)
    )
    if current is not None:
        current.is_default = False
    db.execute(
        update(ModelConfig)
        .where(ModelConfig.kind == kind, ModelConfig.is_default.is_(True))
        .values(is_default=False)
    )
    if existing is None:
        existing = ModelConfig(
            kind=kind,
            name=recommendation.name,
            provider=recommendation.provider,
            enabled=True,
            is_default=True,
            config=dict(recommendation.config),
        )
        db.add(existing)
        action = "created"
    else:
        existing.provider = recommendation.provider
        existing.config = dict(recommendation.config)
        existing.enabled = True
        existing.is_default = True
        action = "updated"
    db.flush()
    return existing, action


def ensure_additional_recommended_config(
    db: Session,
    recommendation: RecommendedModel,
) -> tuple[ModelConfig, str]:
    """Create a useful optional preset without overwriting a user's edits."""
    existing = db.scalar(
        select(ModelConfig).where(
            ModelConfig.kind == recommendation.kind,
            ModelConfig.name == recommendation.name,
        )
    )
    if existing is not None:
        if (
            recommendation.kind == "translation"
            and recommendation.provider == "openai-compatible"
            and not existing.encrypted_api_key
            and dict(existing.config or {}) == LEGACY_CUSTOM_TRANSLATION_TEMPLATE_CONFIG
        ):
            existing.config = dict(recommendation.config)
            db.flush()
            return existing, "updated"
        return existing, "kept"
    # Older releases installed a DeepSeek-specific card even though the runtime
    # provider supports any OpenAI-compatible endpoint. Only migrate the exact,
    # untouched and keyless template so real user configurations are preserved.
    if recommendation.kind == "translation" and recommendation.provider == "openai-compatible":
        legacy = db.scalar(
            select(ModelConfig).where(
                ModelConfig.kind == "translation",
                ModelConfig.name == LEGACY_DEEPSEEK_TEMPLATE_NAME,
                ModelConfig.provider == "openai-compatible",
            )
        )
        if (
            legacy is not None
            and not legacy.encrypted_api_key
            and dict(legacy.config or {}) == LEGACY_DEEPSEEK_TEMPLATE_CONFIG
        ):
            legacy.name = recommendation.name
            legacy.config = dict(recommendation.config)
            db.flush()
            return legacy, "updated"
    item = ModelConfig(
        kind=recommendation.kind,
        name=recommendation.name,
        provider=recommendation.provider,
        enabled=True,
        is_default=False,
        config=dict(recommendation.config),
    )
    db.add(item)
    db.flush()
    return item, "created"


def ensure_selected_config(
    db: Session,
    *,
    kind: str,
    provider: str,
    device: str | None = None,
    config_overrides: dict[str, Any] | None = None,
    api_key: str | None = None,
) -> tuple[ModelConfig, str]:
    """Enable a user-selected provider while preserving its existing secrets and custom settings."""
    recommendation = SELECTABLE_MODELS.get((kind, provider))
    if recommendation is None:
        raise ValueError(f"Provider {provider} is not available for {kind}")
    existing = db.scalar(
        select(ModelConfig)
        .where(ModelConfig.kind == kind, ModelConfig.provider == provider)
        .order_by(ModelConfig.updated_at.desc())
    )
    if existing is None:
        existing = db.scalar(
            select(ModelConfig).where(
                ModelConfig.kind == kind,
                ModelConfig.name == recommendation.name,
            )
        )
    db.execute(
        update(ModelConfig)
        .where(ModelConfig.kind == kind, ModelConfig.is_default.is_(True))
        .values(is_default=False)
    )
    if existing is None:
        config = dict(recommendation.config)
        config.update(dict(config_overrides or {}))
        if device is not None and "device" in config:
            config["device"] = device
        existing = ModelConfig(
            kind=kind,
            name=recommendation.name,
            provider=provider,
            enabled=True,
            is_default=True,
            config=config,
        )
        db.add(existing)
        action = "created"
    else:
        previous = (existing.provider, existing.enabled, existing.is_default, dict(existing.config or {}))
        config = dict(recommendation.config)
        config.update(dict(existing.config or {}))
        config.update(dict(config_overrides or {}))
        if device is not None and "device" in recommendation.config:
            config["device"] = device
        existing.provider = provider
        existing.enabled = True
        existing.is_default = True
        existing.config = config
        current = (existing.provider, existing.enabled, existing.is_default, dict(existing.config or {}))
        action = "kept" if current == previous else "updated"
    if api_key is not None:
        existing.encrypted_api_key = get_secret_store().encrypt(api_key)
    db.flush()
    return existing, action


SETUP_STAGES = ("detection", "inpainting", "ocr", "rendering", "translation")


def _has_rendering_font() -> bool:
    suffixes = {".ttf", ".otf", ".ttc", ".otc"}
    roots = (Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts")
    return any(
        path.is_file() and path.suffix.lower() in suffixes
        for root in roots
        if root.exists()
        for path in root.rglob("*")
    )


def setup_status_report(db: Session, *, validate_models: bool = False) -> dict[str, Any]:
    """Check whether every required workspace provider can actually be used."""
    configured_count = len(list(db.scalars(select(ModelConfig.id)).all()))
    descriptors = {(item["kind"], item["name"]): item for item in registry.describe()}
    stages: list[dict[str, Any]] = []
    for kind in SETUP_STAGES:
        item = db.scalar(
            select(ModelConfig)
            .where(ModelConfig.kind == kind, ModelConfig.enabled.is_(True), ModelConfig.is_default.is_(True))
            .order_by(ModelConfig.updated_at.desc())
        )
        message = ""
        status = "ready"
        if item is None:
            status, message = "missing", "尚未安装默认配置"
        elif (kind, item.provider) not in descriptors:
            status, message = "error", f"Provider {item.provider} 未注册"
        elif not registry.is_installed(kind, item.provider):
            status, message = "error", f"Provider {item.provider} 的运行依赖未安装"
        elif kind == "translation" and item.provider != "openai-compatible":
            status, message = "error", "必须配置真实的云端翻译接口，原文直通不能用于完整工作台"
        elif kind == "translation" and not (
            item.encrypted_api_key and item.config.get("base_url") and item.config.get("model")
        ):
            status, message = "error", "云端翻译缺少 API 地址、密钥或模型"
        elif str(item.config.get("device", "cpu")).lower().startswith(("cuda", "gpu")):
            if kind == "detection" and not paddle_cuda_available():
                status, message = "error", "Paddle CUDA 当前不可用，请改用 CPU 或修复 GPU 环境"
            elif kind in {"ocr", "inpainting"} and not torch_cuda_available():
                status, message = "error", "PyTorch CUDA 当前不可用，请改用 CPU 或修复 GPU 环境"
        if validate_models and status == "ready" and kind != "translation" and item is not None:
            try:
                registry.create(item.kind, item.provider, dict(item.config or {})).ensure_loaded()
            except Exception as exc:
                status, message = "error", str(exc)
        stages.append(
            {
                "kind": kind,
                "provider": item.provider if item is not None else None,
                "status": status,
                "message": message,
            }
        )

    font_ready = _has_rendering_font()
    stages.append(
        {
            "kind": "font",
            "provider": "builtin" if font_ready else None,
            "status": "ready" if font_ready else "error",
            "message": "" if font_ready else "没有找到可用于排版的 TrueType/OpenType 字体",
        }
    )
    return {
        "first_run": configured_count == 0,
        "ready": all(item["status"] == "ready" for item in stages),
        "validated": validate_models,
        "stages": stages,
    }


def bootstrap_recommended_models(
    db: Session,
    *,
    stages: Iterable[str] | None = None,
    preload: bool = False,
    upgrade_fallbacks: bool = False,
    selections: Iterable[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    requested = list(selections or [])
    if requested:
        configs = [
            ensure_selected_config(
                db,
                kind=str(selection["kind"]),
                provider=str(selection["provider"]),
                device=str(selection["device"]) if selection.get("device") else None,
                config_overrides=dict(selection.get("config") or {}),
                api_key=str(selection["api_key"]) if selection.get("api_key") is not None else None,
            )
            for selection in requested
        ]
    else:
        selected = list(stages or RECOMMENDED_MODELS)
        configs = [
            ensure_recommended_config(db, kind, upgrade_fallbacks=upgrade_fallbacks) for kind in selected
        ]
        configs.extend(
            ensure_additional_recommended_config(db, recommendation)
            for recommendation in ADDITIONAL_RECOMMENDED_MODELS
            if recommendation.kind in selected
        )
    db.commit()
    report: list[dict[str, Any]] = []
    for item, action in configs:
        status = "configured"
        error: str | None = None
        installed = registry.is_installed(item.kind, item.provider)
        if item.kind == "translation" and item.provider == "openai-compatible" and not (
            item.encrypted_api_key and item.config.get("base_url") and item.config.get("model")
        ):
            status = "configuration_required"
            error = "请编辑云端翻译配置并填写 API 地址、密钥和模型"
        if preload:
            if status == "configuration_required":
                pass
            elif not installed:
                status = "dependency_missing"
                error = f"Provider dependency for {item.provider} is not installed in this runtime"
            else:
                try:
                    registry.create(item.kind, item.provider, dict(item.config or {})).ensure_loaded()
                    status = "ready"
                except Exception as exc:  # The report must include download/load failures per provider.
                    status = "error"
                    error = str(exc)
        report.append(
            {
                "id": item.id,
                "kind": item.kind,
                "name": item.name,
                "provider": item.provider,
                "action": action,
                "installed": installed,
                "status": status,
                "error": error,
            }
        )
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Install MangaFlow recommended model configurations")
    parser.add_argument("--preload", action="store_true", help="Download weights and load every local provider")
    parser.add_argument("--upgrade-fallbacks", action="store_true", help="Replace fallback defaults")
    args = parser.parse_args()
    initialize_database()
    with SessionLocal() as db:
        report = bootstrap_recommended_models(
            db,
            preload=args.preload,
            upgrade_fallbacks=args.upgrade_fallbacks,
        )
        from app.services.model_manifest import persist_model_settings

        settings = get_settings()
        persist_model_settings(
            db,
            settings.model_manifest_path,
            environment_path=settings.environment_file_path,
        )
    for entry in report:
        detail = f" - {entry['error']}" if entry["error"] else ""
        print(f"{entry['kind']}: {entry['provider']} [{entry['status']}]{detail}")
    return 1 if any(entry["status"] in {"error", "dependency_missing"} for entry in report) else 0


if __name__ == "__main__":
    raise SystemExit(main())

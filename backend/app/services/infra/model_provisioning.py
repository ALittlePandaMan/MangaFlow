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
from app.services.base import ProviderError
from app.services.infra.device import effective_device_request, paddle_cuda_available, torch_cuda_available
from app.services.ocr.generic import GENERIC_OCR_DEFAULTS, GenericCloudOCRProvider
from app.services.ocr.local_catalog import LOCAL_OCR_DEFAULT_MAX_TOKENS
from app.services.ocr.prompts import DEFAULT_OCR_PROMPT
from app.services.registry import registry
from app.services.translation.google_web import GOOGLE_WEB_DEFAULTS
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
        "推荐漫画版面检测（RF-DETR）",
        "rfdetr",
        {
            "device": "auto",
            "text_threshold": 0.25,
            "bubble_threshold": 0.5,
            "panel_threshold": 0.5,
            "containment_threshold": 0.9,
            "text_box_padding": {"top": 3.0, "bottom": 3.0, "left": 3.0, "right": 3.0},
        },
    ),
    "ocr": RecommendedModel(
        "ocr",
        "PaddleOCR-VL 1.6（Koharu 默认）",
        "paddleocr-vl-1.6",
        {"device": "auto", "max_new_tokens": LOCAL_OCR_DEFAULT_MAX_TOKENS},
    ),
    "translation": RecommendedModel(
        "translation", "安全翻译占位（需配置真实模型）", "passthrough", {"review_required": True}
    ),
    "inpainting": RecommendedModel(
        "inpainting", "推荐复杂修复（LaMa）", "lama", {"device": "auto"}
    ),
    "rendering": RecommendedModel("rendering", "默认排版渲染（Pillow）", "pillow", {"min_font_size": 10}),
}

SELECTABLE_MODELS: dict[tuple[str, str], RecommendedModel] = {
    (item.kind, item.provider): item for item in RECOMMENDED_MODELS.values()
}
SELECTABLE_MODELS.update(
    {
        ("translation", "google-web"): RecommendedModel(
            "translation", "Google 网页翻译（免配置）", "google-web", dict(GOOGLE_WEB_DEFAULTS)
        ),
        ("ocr", "manga-ocr"): RecommendedModel(
            "ocr", "MangaOCR 漫画识别", "manga-ocr", {"device": "auto", "model": "kha-white/manga-ocr-base"}
        ),
        ("ocr", "baberu-ocr"): RecommendedModel("ocr", "Baberu OCR", "baberu-ocr", {"device": "auto", "max_new_tokens": LOCAL_OCR_DEFAULT_MAX_TOKENS}),
        ("ocr", "hayai-ocr"): RecommendedModel("ocr", "Hayai OCR", "hayai-ocr", {"device": "auto", "max_new_tokens": LOCAL_OCR_DEFAULT_MAX_TOKENS}),
        ("detection", "opencv-fallback"): RecommendedModel(
            "detection", "本地 OpenCV 文本检测", "opencv-fallback", {"device": "cpu"}
        ),
        ("ocr", "paddleocr"): RecommendedModel(
            "ocr", "PaddleOCR 文字识别", "paddleocr", {"device": "auto", "language": "japan"}
        ),
        ("ocr", "tesseract"): RecommendedModel(
            "ocr", "Tesseract OCR", "tesseract", {"device": "cpu"}
        ),
        ("ocr", "review-fallback"): RecommendedModel(
            "ocr", "人工审核占位 OCR", "review-fallback", {}
        ),
        ("inpainting", "opencv"): RecommendedModel(
            "inpainting", "OpenCV 快速修复", "opencv", {"device": "cpu"}
        ),
    }
)

ADDITIONAL_RECOMMENDED_MODELS = (
    RecommendedModel("ocr", "云端 OCR（通用接口）", "cloud-ocr", dict(GENERIC_OCR_DEFAULTS)),
    RecommendedModel(
        "ocr", "云端多模态 OCR", "cloud-multimodal",
        {"api_protocol": "auto", "timeout": 90, "retries": 2, "max_tokens": 2048, "prompt": DEFAULT_OCR_PROMPT},
    ),
    RecommendedModel(
        "translation",
        "自定义云端翻译",
        "openai-compatible",
        {
            "api_protocol": "auto",
            "timeout": 90,
            "retries": 2,
            "temperature": 0.2,
            "max_tokens": 2048,
            "prompt": DEFAULT_TRANSLATION_PROMPT,
        },
    ),
)
for _additional_model in ADDITIONAL_RECOMMENDED_MODELS:
    SELECTABLE_MODELS[(_additional_model.kind, _additional_model.provider)] = _additional_model

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
    recommendation = RECOMMENDED_MODELS[kind]
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
        return existing, "kept"
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
    from app.services.ocr.local_catalog import LOCAL_OCR_MODELS, validate_local_config
    if kind == "ocr" and provider in LOCAL_OCR_MODELS:
        validate_local_config(provider, {**recommendation.config, **(config_overrides or {})})
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
        elif kind == "translation" and item.provider == "passthrough":
            status, message = "error", "必须选择可用的翻译服务，原文直通不能用于完整工作台"
        elif ((kind == "translation" and item.provider == "openai-compatible") or (kind == "ocr" and item.provider == "cloud-multimodal")) and not (
            item.encrypted_api_key and item.config.get("base_url") and item.config.get("model")
        ):
            status, message = "error", f"{'云端 OCR' if kind == 'ocr' else '云端翻译'}缺少 API 地址、密钥或模型"
        elif kind == "ocr" and item.provider == "cloud-ocr":
            try:
                config = dict(item.config or {})
                if item.encrypted_api_key:
                    config["api_key"] = get_secret_store().decrypt(item.encrypted_api_key)
                GenericCloudOCRProvider(config).ensure_loaded()
            except ProviderError as exc:
                status, message = "error", str(exc)
        elif effective_device_request(item.config.get("device", "cpu")).startswith(("cuda", "gpu")):
            if kind == "ocr" and item.provider == "paddleocr" and not paddle_cuda_available():
                status, message = "error", "Paddle CUDA 当前不可用，请改用 CPU 或修复 GPU 环境"
            elif (kind == "inpainting" or (kind == "ocr" and item.provider != "paddleocr") or (kind == "detection" and item.provider == "rfdetr")) and not torch_cuda_available():
                status, message = "error", "PyTorch CUDA 当前不可用，请改用 CPU 或修复 GPU 环境"
        if validate_models and status == "ready" and kind != "translation" and item is not None:
            try:
                config = dict(item.config or {})
                if item.encrypted_api_key:
                    config["api_key"] = get_secret_store().decrypt(item.encrypted_api_key)
                registry.create(item.kind, item.provider, config).ensure_loaded()
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
        if item.provider in {"openai-compatible", "cloud-multimodal"} and not (
            item.encrypted_api_key and item.config.get("base_url") and item.config.get("model")
        ):
            status = "configuration_required"
            error = f"请编辑{'云端 OCR' if item.kind == 'ocr' else '云端翻译'}配置并填写 API 地址、密钥和模型"
        if item.provider == "cloud-ocr":
            try:
                config = {**item.config, "api_key": get_secret_store().decrypt(item.encrypted_api_key)}
                GenericCloudOCRProvider(config).ensure_loaded()
            except ProviderError as exc:
                status, error = "configuration_required", str(exc)
        if preload:
            if status == "configuration_required":
                pass
            elif not installed:
                status = "dependency_missing"
                error = f"Provider dependency for {item.provider} is not installed in this runtime"
            else:
                try:
                    config = dict(item.config or {})
                    if item.encrypted_api_key:
                        config["api_key"] = get_secret_store().decrypt(item.encrypted_api_key)
                    registry.create(item.kind, item.provider, config).ensure_loaded()
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
        from app.services.infra.model_manifest import persist_model_settings

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

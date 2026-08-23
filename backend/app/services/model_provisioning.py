from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.core.database import SessionLocal, initialize_database
from app.models import ModelConfig
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


def bootstrap_recommended_models(
    db: Session,
    *,
    stages: Iterable[str] | None = None,
    preload: bool = False,
    upgrade_fallbacks: bool = False,
) -> list[dict[str, Any]]:
    selected = list(stages or RECOMMENDED_MODELS)
    configs: list[tuple[ModelConfig, str]] = [
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
        if preload:
            if not installed:
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
    for entry in report:
        detail = f" - {entry['error']}" if entry["error"] else ""
        print(f"{entry['kind']}: {entry['provider']} [{entry['status']}]{detail}")
    return 1 if any(entry["status"] in {"error", "dependency_missing"} for entry in report) else 0


if __name__ == "__main__":
    raise SystemExit(main())

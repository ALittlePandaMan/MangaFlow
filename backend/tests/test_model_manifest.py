from __future__ import annotations

from copy import deepcopy

import pytest
import yaml
from app.core.database import Base
from app.core.secrets import SecretStore
from app.models import ModelConfig
from app.services.infra import model_manifest as manifests
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session


def manifest_payload(*, lightweight: bool = False) -> dict:
    if lightweight:
        detection = {"provider": "opencv-fallback", "device": "recommended", "config": {}}
        inpainting = {"provider": "opencv", "device": "recommended", "config": {"radius": 3.0}}
        ocr = {"provider": "review-fallback", "device": "recommended", "config": {}}
    else:
        detection = {
            "provider": "paddleocr",
            "device": "recommended",
            "config": {"language": "japan", "ocr_version": "PP-OCRv5"},
        }
        inpainting = {"provider": "lama", "device": "recommended", "config": {}}
        ocr = {
            "provider": "manga-ocr",
            "device": "recommended",
            "config": {"model": "kha-white/manga-ocr-base"},
        }
    return {
        "schema_version": 1,
        "apply": "fill-missing",
        "preload": True,
        "stages": {
            "detection": detection,
            "inpainting": inpainting,
            "ocr": ocr,
            "rendering": {"provider": "pillow", "config": {"min_font_size": 10}},
            "translation": {
                "provider": "openai-compatible",
                "config": {
                    "api_protocol": "auto",
                    "base_url": "https://translation.example.com/v1",
                    "model": "manga-translator",
                },
                "api_key_env": "MANGAFLOW_TRANSLATION_API_KEY",
            },
            "font": {"provider": "builtin-noto-cjk"},
        },
    }


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def test_load_model_manifest_validates_version_stages_and_registered_provider(tmp_path) -> None:
    path = tmp_path / "config.example.yaml"
    path.write_text(yaml.safe_dump(manifest_payload(), allow_unicode=True, sort_keys=False), encoding="utf-8")

    loaded = manifests.load_model_manifest(path)

    assert loaded.schema_version == 1
    assert tuple(loaded.stages) == manifests.MANIFEST_STAGES

    invalid = manifest_payload()
    invalid["stages"]["detection"]["provider"] = "manga-ocr"
    path.write_text(yaml.safe_dump(invalid, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(manifests.ModelManifestError, match="not registered for stage 'detection'"):
        manifests.load_model_manifest(path)

    missing_font = manifest_payload()
    del missing_font["stages"]["font"]
    path.write_text(yaml.safe_dump(missing_font, allow_unicode=True, sort_keys=False), encoding="utf-8")
    with pytest.raises(manifests.ModelManifestError, match="missing stages: font"):
        manifests.load_model_manifest(path)


def test_apply_manifest_resolves_recommended_devices_encrypts_secret_and_is_idempotent(
    db: Session,
    monkeypatch,
) -> None:
    secret_store = SecretStore(Fernet.generate_key())
    monkeypatch.setattr(manifests, "get_secret_store", lambda: secret_store)
    monkeypatch.setattr(manifests, "paddle_cuda_available", lambda: True)
    monkeypatch.setattr(manifests, "torch_cuda_available", lambda: False)
    monkeypatch.setenv("MANGAFLOW_TRANSLATION_API_KEY", "portable-secret")
    manifest = manifests.ModelManifest.model_validate(manifest_payload())

    first_report = manifests.apply_model_manifest(db, manifest)
    configured = {
        item.kind: item
        for item in db.scalars(select(ModelConfig).where(ModelConfig.is_default.is_(True))).all()
    }

    assert len(first_report) == 6
    assert set(configured) == set(manifests.MODEL_STAGES)
    assert configured["detection"].config["device"] == "cuda:0"
    assert configured["ocr"].config["device"] == "cpu"
    assert configured["inpainting"].config["device"] == "cpu"
    assert configured["rendering"].config["device"] == "cpu"
    assert "device" not in configured["translation"].config
    ciphertext = configured["translation"].encrypted_api_key
    assert ciphertext and ciphertext != "portable-secret"
    assert secret_store.decrypt(ciphertext) == "portable-secret"

    configured["detection"].config = {**configured["detection"].config, "user_setting": 42}
    db.commit()
    monkeypatch.delenv("MANGAFLOW_TRANSLATION_API_KEY")
    second_report = manifests.apply_model_manifest(db, manifest)

    assert db.scalar(select(func.count()).select_from(ModelConfig)) == 5
    assert all(item["action"] == "kept" for item in second_report)
    assert configured["detection"].config["user_setting"] == 42
    assert configured["translation"].encrypted_api_key == ciphertext


def test_apply_manifest_only_fills_missing_default_and_preserves_existing_choice(db: Session, monkeypatch) -> None:
    monkeypatch.setattr(manifests, "paddle_cuda_available", lambda: True)
    monkeypatch.setattr(manifests, "torch_cuda_available", lambda: True)
    existing = ModelConfig(
        kind="detection",
        name="用户选择的检测器",
        provider="opencv-fallback",
        enabled=True,
        is_default=True,
        config={"device": "cpu", "threshold": 0.7},
    )
    db.add(existing)
    db.commit()

    report = manifests.apply_model_manifest(db, manifests.ModelManifest.model_validate(manifest_payload()))
    detection = db.scalar(
        select(ModelConfig).where(
            ModelConfig.kind == "detection",
            ModelConfig.enabled.is_(True),
            ModelConfig.is_default.is_(True),
        )
    )

    assert detection is not None
    assert detection.id == existing.id
    assert detection.provider == "opencv-fallback"
    assert detection.config == {"device": "cpu", "threshold": 0.7}
    assert next(item for item in report if item["kind"] == "detection")["action"] == "kept"


def test_apply_manifest_does_not_clear_existing_secret_when_environment_is_missing(db: Session, monkeypatch) -> None:
    secret_store = SecretStore(Fernet.generate_key())
    monkeypatch.setattr(manifests, "get_secret_store", lambda: secret_store)
    monkeypatch.delenv("MANGAFLOW_TRANSLATION_API_KEY", raising=False)
    existing = ModelConfig(
        kind="translation",
        name="已有云端翻译",
        provider="openai-compatible",
        enabled=True,
        is_default=True,
        config={"base_url": "https://old.example.com/v1", "model": "old-model"},
        encrypted_api_key=secret_store.encrypt("existing-secret"),
    )
    db.add(existing)
    db.commit()
    ciphertext = existing.encrypted_api_key

    manifests.apply_model_manifest(db, manifests.ModelManifest.model_validate(manifest_payload(lightweight=True)))

    assert existing.encrypted_api_key == ciphertext
    assert secret_store.decrypt(existing.encrypted_api_key) == "existing-secret"
    assert existing.config["model"] == "old-model"


def test_manifest_rejects_nonportable_or_unsupported_device() -> None:
    invalid = manifest_payload()
    invalid["stages"]["detection"].pop("device")
    invalid["stages"]["detection"]["config"]["device"] = "auto"
    manifest = manifests.ModelManifest.model_validate(invalid)

    with pytest.raises(manifests.ModelManifestError, match="Unsupported device 'auto'"):
        manifests.apply_model_manifest(Session(create_engine("sqlite:///:memory:")), manifest)

    incompatible = manifest_payload(lightweight=True)
    incompatible["stages"]["detection"]["device"] = "cuda:0"
    with pytest.raises(manifests.ModelManifestError, match="does not support device 'cuda:0'"):
        manifests.ModelManifest.model_validate(incompatible)
        manifests._resolved_manifest_configs(manifests.ModelManifest.model_validate(incompatible))


def test_preload_manifest_models_reports_each_stage_and_isolates_failures(
    db: Session,
    monkeypatch,
) -> None:
    manifest = manifests.ModelManifest.model_validate(manifest_payload(lightweight=True))
    manifests.apply_model_manifest(db, manifest)
    loaded: list[str] = []

    class FakeProvider:
        def __init__(self, kind: str) -> None:
            self.kind = kind

        def ensure_loaded(self) -> None:
            loaded.append(self.kind)
            if self.kind == "ocr":
                raise RuntimeError("simulated OCR download failure")

    monkeypatch.setattr(manifests.registry, "is_installed", lambda _kind, _provider: True)
    monkeypatch.setattr(
        manifests.registry,
        "create",
        lambda kind, _provider, _config: FakeProvider(kind),
    )

    report = manifests.preload_manifest_models(db, manifest)
    statuses = {item["kind"]: item["status"] for item in report}

    assert loaded == ["detection", "inpainting", "ocr", "rendering"]
    assert statuses == {
        "detection": "ready",
        "inpainting": "ready",
        "ocr": "error",
        "rendering": "ready",
        "translation": "skipped",
        "font": "skipped",
    }
    assert "simulated OCR download failure" in next(item for item in report if item["kind"] == "ocr")["error"]


def test_preload_false_skips_all_stages_without_loading(db: Session, monkeypatch) -> None:
    payload = deepcopy(manifest_payload(lightweight=True))
    payload["preload"] = False
    manifest = manifests.ModelManifest.model_validate(payload)
    manifests.apply_model_manifest(db, manifest)
    monkeypatch.setattr(
        manifests.registry,
        "create",
        lambda *_args, **_kwargs: pytest.fail("provider must not load when preload is disabled"),
    )

    report = manifests.preload_manifest_models(db, manifest)

    assert len(report) == 6
    assert {item["status"] for item in report} == {"skipped"}


def test_persist_model_settings_writes_yaml_and_keeps_secret_only_in_env(
    db: Session,
    monkeypatch,
    tmp_path,
) -> None:
    secret_store = SecretStore(Fernet.generate_key())
    monkeypatch.setattr(manifests, "get_secret_store", lambda: secret_store)
    monkeypatch.setattr(manifests, "paddle_cuda_available", lambda: False)
    monkeypatch.setattr(manifests, "torch_cuda_available", lambda: False)
    monkeypatch.setenv("MANGAFLOW_TRANSLATION_API_KEY", "portable-secret")
    manifests.apply_model_manifest(db, manifests.ModelManifest.model_validate(manifest_payload()))
    translation = db.scalar(select(ModelConfig).where(ModelConfig.kind == "translation"))
    assert translation is not None
    translation.config = {
        **translation.config,
        "headers": {"Authorization": "Bearer must-not-leak"},
        "api_key": "must-not-leak",
    }
    db.commit()
    manifest_path = tmp_path / "config.yaml"
    environment_path = tmp_path / ".env"
    environment_path.write_text("MANGAFLOW_DEBUG=false\nMANGAFLOW_TRANSLATION_API_KEY=old\n", encoding="utf-8")

    saved = manifests.persist_model_settings(
        db,
        manifest_path,
        environment_path=environment_path,
    )

    assert saved is not None
    yaml_text = manifest_path.read_text(encoding="utf-8")
    assert "portable-secret" not in yaml_text
    assert "must-not-leak" not in yaml_text
    reloaded = manifests.load_model_manifest(manifest_path)
    assert reloaded.stages["translation"].config["base_url"] == "https://translation.example.com/v1"
    assert reloaded.stages["translation"].api_key_env == "MANGAFLOW_TRANSLATION_API_KEY"
    environment_text = environment_path.read_text(encoding="utf-8")
    assert 'MANGAFLOW_TRANSLATION_API_KEY="portable-secret"' in environment_text
    assert "MANGAFLOW_DEBUG=false" in environment_text

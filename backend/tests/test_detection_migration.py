from copy import deepcopy

from app.models import ModelConfig
from app.services.infra.detection_migration import migrate_retired_detection_configs
from app.services.infra.model_manifest import ModelManifest, load_model_manifest
from app.services.registry import ProviderRegistry
from sqlalchemy.orm import Session


def test_migrates_only_retired_detectors_preserving_defaults_ids_and_ocr(model_engine):
    with Session(model_engine, expire_on_commit=False) as db:
        legacy = ModelConfig(kind="detection", name="old detector", provider="paddleocr", is_default=True,
                             config={"device": "cpu", "bubble_grouping": True, "text_classification": False})
        ocr = ModelConfig(kind="ocr", name="Paddle OCR", provider="paddleocr", config={"language": "japan"})
        native = ModelConfig(kind="detection", name="native", provider="rfdetr", is_default=False,
                             config={"text_threshold": .7})
        db.add_all([legacy, ocr, native])
        db.commit()
        legacy_id = legacy.id
        assert migrate_retired_detection_configs(db) == 1
        assert legacy.id == legacy_id and legacy.is_default and legacy.enabled
        assert legacy.provider == "rfdetr" and legacy.config["device"] == "cpu"
        assert legacy.config["text_threshold"] == .25
        assert "bubble_grouping" not in legacy.config and "text_classification" not in legacy.config
        assert ocr.provider == "paddleocr" and ocr.config == {"language": "japan"}
        assert native.config == {"text_threshold": .7} and not native.is_default
        assert migrate_retired_detection_configs(db) == 0


def test_old_manifest_migrates_before_provider_validation(tmp_path):
    import yaml
    from test_model_manifest import manifest_payload

    payload = deepcopy(manifest_payload())
    payload["stages"]["detection"] = {"provider": "paddleocr", "device": "cpu", "config": {
        "box_threshold": .8, "bubble_grouping": {"enabled": True},
    }}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    for manifest in (ModelManifest.model_validate(payload), load_model_manifest(path)):
        assert manifest.stages["detection"].provider == "rfdetr"
        assert manifest.stages["detection"].device == "cpu"
        assert manifest.stages["detection"].config == {
            "text_threshold": .25, "bubble_threshold": .5, "panel_threshold": .5, "containment_threshold": .9,
        }


def test_only_native_and_lightweight_detectors_are_registered():
    assert set(ProviderRegistry.detectors) == {"rfdetr", "opencv-fallback"}
    assert "paddleocr" in ProviderRegistry.ocr

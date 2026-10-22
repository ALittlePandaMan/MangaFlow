from __future__ import annotations

from unittest.mock import Mock
from zipfile import ZipFile

import pytest
from app.core.database import get_db
from app.main import app
from app.models import ModelConfig
from app.services.infra import recommended_installation as setup
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture
def installation(monkeypatch, model_engine):
    manager = setup.RecommendedInstallation()
    cached: set[str] = set()
    launched = []

    class DeferredThread:
        def __init__(self, *, target, **kwargs):
            self.run = target

        def start(self):
            launched.append(self)

    monkeypatch.setattr(setup, "Thread", DeferredThread)
    monkeypatch.setattr(setup, "_dependency_ready", lambda *args: True)
    monkeypatch.setattr(setup, "_assets_ready", lambda provider: provider in cached)
    download = Mock(side_effect=cached.add)
    monkeypatch.setattr(setup, "_download", download)
    monkeypatch.setattr(setup, "SessionLocal", lambda: Session(model_engine))
    persist = Mock()
    monkeypatch.setattr(setup, "persist_model_settings", persist)
    monkeypatch.setattr(setup, "recommended_installation", manager)

    def database():
        with Session(model_engine) as db:
            yield db

    monkeypatch.setitem(app.dependency_overrides, get_db, database)
    return manager, cached, launched, download, persist


def add_config(db, kind, provider, **kwargs):
    item = ModelConfig(kind=kind, provider=provider, name=provider, enabled=True, is_default=True, config=kwargs)
    db.add(item)
    db.commit()
    return item


def test_status_is_offline_and_does_not_mistake_configurations_for_downloads(client, installation, model_engine):
    _, _, launched, download, _ = installation
    with Session(model_engine) as db:
        for kind, provider, _ in setup.LOCAL_SETUP:
            add_config(db, kind, provider)
    result = client.get("/api/models/recommended/status").json()
    assert result["installed"] is False
    assert result["configured"] is True
    assert len(result["models"]) == 4
    assert result["state"] == "idle"
    download.assert_not_called()
    assert not launched


def test_cached_assets_do_not_require_a_matching_default(client, installation):
    _, cached, _, download, _ = installation
    cached.update(provider for _, provider, _ in setup.LOCAL_SETUP)
    result = client.get("/api/models/recommended/status").json()
    assert result["installed"] is True
    assert result["configured"] is False
    download.assert_not_called()


def test_confirmed_install_downloads_only_missing_assets_and_preserves_translation(client, installation, model_engine):
    _, cached, launched, download, persist = installation
    cached.update({"rfdetr", "pillow"})
    with Session(model_engine) as db:
        translation = add_config(db, "translation", "google-web", custom="keep")
        translation_id = translation.id
        add_config(db, "detection", "rfdetr", text_threshold=.6, device="cpu")
        add_config(db, "ocr", "manga-ocr", device="cpu")
    response = client.post("/api/models/recommended/install")
    assert response.status_code == 202
    assert response.json()["state"] == "installing"
    # Start requests are idempotent while the same process owns the job.
    assert client.post("/api/models/recommended/install").status_code == 202
    assert len(launched) == 1
    download.assert_not_called()
    launched[0].run()
    assert [call.args[0] for call in download.call_args_list] == ["paddleocr-vl-1.6", "lama"]
    result = client.get("/api/models/recommended/status").json()
    assert result["installed"] and result["configured"]
    assert result["state"] == "complete"
    with Session(model_engine) as db:
        defaults = {item.kind: item for item in db.scalars(select(ModelConfig).where(ModelConfig.is_default.is_(True)))}
        assert {kind: item.provider for kind, item in defaults.items()} == {
            **{kind: provider for kind, provider, _ in setup.LOCAL_SETUP}, "translation": "google-web",
        }
        assert defaults["translation"].id == translation_id
        assert defaults["translation"].config == {"custom": "keep"}
        assert defaults["detection"].config["text_threshold"] == .6
        assert defaults["detection"].config["device"] == "cpu"
        assert defaults["ocr"].config["max_new_tokens"] == 2048
        assert db.scalar(select(ModelConfig).where(ModelConfig.provider == "manga-ocr")) is not None
    persist.assert_called_once()


def test_partial_failure_never_replaces_defaults_and_retry_reuses_completed_downloads(client, installation, model_engine):
    _, cached, launched, download, _ = installation
    cached.add("pillow")
    with Session(model_engine) as db:
        add_config(db, "detection", "opencv-fallback", device="cpu")

    def fail_ocr(provider):
        if provider == "paddleocr-vl-1.6":
            raise OSError("OCR download failed")
        cached.add(provider)

    download.side_effect = fail_ocr
    client.post("/api/models/recommended/install")
    launched[0].run()
    result = client.get("/api/models/recommended/status").json()
    assert result["state"] == "error" and not result["installed"]
    assert result["error"] == "OCR download failed"
    with Session(model_engine) as db:
        assert db.scalar(select(ModelConfig).where(ModelConfig.is_default.is_(True))).provider == "opencv-fallback"
        assert len(list(db.scalars(select(ModelConfig)))) == 1
    assert "rfdetr" in cached
    download.reset_mock(side_effect=True)
    download.side_effect = cached.add
    client.post("/api/models/recommended/install")
    launched[1].run()
    assert [call.args[0] for call in download.call_args_list] == ["paddleocr-vl-1.6", "lama"]
    assert client.get("/api/models/recommended/status").json()["state"] == "complete"


def test_cached_install_only_configures_without_downloading(client, installation):
    _, cached, launched, download, _ = installation
    cached.update(provider for _, provider, _ in setup.LOCAL_SETUP)
    client.post("/api/models/recommended/install")
    launched[0].run()
    download.assert_not_called()
    assert client.get("/api/models/recommended/status").json()["configured"]


def test_missing_runtime_is_an_error_not_a_successful_install(client, installation, monkeypatch):
    _, cached, launched, download, _ = installation
    cached.update(provider for _, provider, _ in setup.LOCAL_SETUP)
    monkeypatch.setattr(setup, "_dependency_ready", lambda kind, provider: provider != "rfdetr")
    assert not client.get("/api/models/recommended/status").json()["installed"]
    client.post("/api/models/recommended/install")
    launched[0].run()
    result = client.get("/api/models/recommended/status").json()
    assert result["state"] == "error"
    assert "运行依赖未安装" in result["error"]
    download.assert_not_called()


def test_polling_running_download_never_rescans_weights(client, installation, monkeypatch):
    client.post("/api/models/recommended/install")
    scan = Mock(side_effect=AssertionError("Do not hash/rescan assets while a download is running"))
    monkeypatch.setattr(setup, "_local_status", scan)
    assert client.get("/api/models/recommended/status").json()["state"] == "installing"
    scan.assert_not_called()


def test_offline_ocr_status_requires_the_complete_pinned_snapshot(monkeypatch, tmp_path):
    from app.services.ocr import local_catalog

    def incomplete(*args, **kwargs):
        assert kwargs == {"download": False}
        raise FileNotFoundError("processor missing")

    monkeypatch.setattr(setup, "resolve_assets", incomplete)
    assert not setup._assets_ready("paddleocr-vl-1.6")
    weights = tmp_path / "model.safetensors"
    weights.touch()
    monkeypatch.setattr(setup, "resolve_assets", lambda *args, **kwargs: (tmp_path, None))
    assert not setup._assets_ready("paddleocr-vl-1.6")
    assert "processor_config.json" in local_catalog.LOCAL_OCR_MODELS["paddleocr-vl-1.6"].files


def test_rfdetr_checksum_status_invalidates_when_file_changes(monkeypatch, tmp_path):
    import hashlib
    from types import SimpleNamespace

    path = tmp_path / setup.MODEL_RELATIVE_PATH
    path.parent.mkdir(parents=True)
    path.write_bytes(b"verified model")
    monkeypatch.setattr(setup, "get_settings", lambda: SimpleNamespace(model_dir=tmp_path))
    monkeypatch.setattr(setup, "MODEL_SHA256", hashlib.sha256(b"verified model").hexdigest())
    setup._verified_detection_file.cache_clear()
    assert setup._assets_ready("rfdetr")
    path.write_bytes(b"truncated")
    assert not setup._assets_ready("rfdetr")
    setup._verified_detection_file.cache_clear()


def test_lama_status_rejects_partial_or_non_torchscript_archives(tmp_path):
    path = tmp_path / "big-lama.pt"
    assert not setup._valid_lama_file(path)
    path.write_bytes(b"partial download")
    assert not setup._valid_lama_file(path)
    with ZipFile(path, "w") as archive:
        archive.writestr("model/data.pkl", b"test")
    assert not setup._valid_lama_file(path)
    with ZipFile(path, "a") as archive:
        archive.writestr("model/constants.pkl", b"test")
    assert setup._valid_lama_file(path)

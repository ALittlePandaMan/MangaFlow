from types import SimpleNamespace

import yaml
from app.api import preferences as preferences_api


def _manifest_payload() -> dict:
    return {
        "schema_version": 1,
        "apply": "fill-missing",
        "preload": False,
        "stages": {
            "detection": {"provider": "opencv-fallback", "device": "cpu", "config": {}},
            "inpainting": {"provider": "opencv", "device": "cpu", "config": {}},
            "ocr": {"provider": "review-fallback", "device": "cpu", "config": {}},
            "rendering": {"provider": "pillow", "device": "cpu", "config": {}},
            "translation": {"provider": "passthrough", "config": {}},
            "font": {"provider": "builtin-noto-cjk", "config": {}},
        },
    }


def test_preferences_api_round_trips_shortcuts_through_yaml(client, monkeypatch, tmp_path) -> None:
    manifest_path = tmp_path / "config.yaml"
    manifest_path.write_text(yaml.safe_dump(_manifest_payload(), sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(
        preferences_api,
        "get_settings",
        lambda: SimpleNamespace(model_manifest_path=manifest_path),
    )

    initial = client.get("/api/preferences")
    assert initial.status_code == 200
    assert initial.json() == {"shortcuts": {}}

    updated = client.put(
        "/api/preferences",
        json={"shortcuts": {"tool.select": "KeyS", "page.export": "Mod+Shift+KeyE"}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["shortcuts"]["tool.select"] == "KeyS"

    reloaded = client.get("/api/preferences")
    assert reloaded.json() == updated.json()
    saved = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    assert saved["preferences"]["shortcuts"]["page.export"] == "Mod+Shift+KeyE"

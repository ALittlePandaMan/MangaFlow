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


def test_text_style_and_shortcut_updates_preserve_each_other(client, monkeypatch, tmp_path) -> None:
    path = tmp_path / "config.yaml"
    original = _manifest_payload()
    path.write_text(yaml.safe_dump(original), encoding="utf-8")
    monkeypatch.setattr(preferences_api, "get_settings", lambda: SimpleNamespace(model_manifest_path=path))
    assert client.put("/api/preferences", json={"shortcuts": {"tool.select": "KeyS"}}).status_code == 200
    response = client.put("/api/preferences", json={"text_style": {"font_size": 42, "orientation": "horizontal", "auto_font_size": False}})
    assert response.status_code == 200, response.text
    assert response.json()["shortcuts"] == {"tool.select": "KeyS"}
    response = client.put("/api/preferences", json={"shortcuts": {"tool.select": "KeyV"}})
    assert response.json()["text_style"]["font_size"] == 42
    assert response.json()["text_style"]["auto_font_size"] is False
    assert client.get("/api/preferences").json() == response.json()
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["stages"] == original["stages"]


def test_text_style_rejects_invalid_values(client) -> None:
    for style in ({"font_size": 0}, {"line_spacing": 0}, {"text_color": "bad"}, {"orientation": "diagonal"}):
        assert client.put("/api/preferences", json={"text_style": style}).status_code == 422

from pathlib import Path

import pytest
from app.storage import get_storage
from app.storage.media import public_media_path


@pytest.mark.parametrize("path", ["mangaflow.db", "mangaflow.db-wal", "execution-policy.json", ".env", "cache/state.json", "projects/p/settings.json", "projects/p/pages/i/original/leak.db", "projects/p/pages/i/original/../leak.png", "projects/p/history/assets/.secret.png", "fonts/../mangaflow.db", "projects\\p\\pages\\i\\original\\a.png"])
def test_private_paths_not_public(path):
    assert not public_media_path(path)


@pytest.mark.parametrize("path", ["fonts/a.ttf", "projects/p/fonts/a.otf", "projects/p/pages/i/original/a.webp", "projects/p/pages/i/masks/a.png", "projects/p/history/assets/a.png"])
def test_editing_assets_public(path):
    assert public_media_path(path)


def test_media_blocks_database_get_and_head_but_preserves_images(client):
    storage = get_storage()
    private = storage.root / "private.db"
    private.write_bytes(b"private data")
    asset = storage.page_dir("media-security", "image") / "original" / "page.png"
    asset.write_bytes(b"test image")
    for method in (client.get, client.head):
        assert method("/media/mangaflow.db").status_code == 404
        assert method("/media/private.db").status_code == 404
        assert method("/media/execution-policy.json").status_code == 404
    response = client.get(f"/media/{storage.relative(asset)}?v=1")
    assert response.status_code == 200
    assert response.content == b"test image"
    assert "immutable" in response.headers["cache-control"]
    # A symlink into private files cannot bypass StaticFiles' containment check.
    link = asset.parent / "escape.png"
    try:
        link.symlink_to(Path("/etc/passwd"))
    except OSError:
        return
    assert client.get(f"/media/{storage.relative(asset.parent)}/escape.png").status_code == 404
    internal = asset.parent / "private-link.png"
    internal.symlink_to(private)
    assert client.get(f"/media/{storage.relative(asset.parent)}/private-link.png").status_code == 404

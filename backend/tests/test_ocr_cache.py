from pathlib import Path

from app.services.ocr import providers
from PIL import Image


def test_region_crops_reuse_the_decoded_page(tmp_path: Path, monkeypatch) -> None:
    image_path = tmp_path / "page.png"
    Image.new("RGB", (180, 120), "white").save(image_path, "PNG")
    providers._decoded_page.cache_clear()
    calls = 0
    original_open = providers.Image.open

    def counted_open(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original_open(*args, **kwargs)

    monkeypatch.setattr(providers.Image, "open", counted_open)
    first = providers._crop(image_path, [10, 10, 40, 50], 4)
    second = providers._crop(image_path, [80, 20, 45, 60], 4)

    assert first.size == (48, 58)
    assert second.size == (53, 68)
    assert calls == 1

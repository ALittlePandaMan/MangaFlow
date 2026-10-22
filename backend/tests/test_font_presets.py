from __future__ import annotations

import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import httpx
import pytest
from app.services import font_presets
from app.services.layout import FontResolver
from app.storage import StorageManager
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont


def test_global_upload_without_family_name_uses_original_filename(client, asset):
    _, payload, directory = asset
    buffer = io.BytesIO()
    with TTFont(io.BytesIO(payload)) as font:
        font["name"].names = [name for name in font["name"].names if name.nameID not in (1, 16)]
        font.save(buffer)
    response = client.post("/api/fonts", files={"file": ("Original Family.ttf", buffer.getvalue(), "font/ttf")})
    assert response.status_code == 201, response.text
    assert response.json()["filename"] == "OriginalFamily.ttf"
    assert response.json()["name"] == "OriginalFamily"
    assert not list(directory.glob(".upload-*"))


def test_project_upload_uses_shared_family_and_preserves_font_on_invalid_replacement(client, asset):
    from app.services.font_library import font_family, font_library_revision
    from app.storage import get_storage

    _, payload, _ = asset
    project = client.post("/api/projects", json={"name": "Font upload"}).json()
    endpoint = f"/api/projects/{project['id']}/fonts"
    before = font_library_revision()
    response = client.post(endpoint, files={"file": ("family.ttf", payload, "font/ttf")})
    assert response.status_code == 201, response.text
    entry = response.json()
    target = get_storage().absolute(entry["path"])
    assert entry["name"] == font_family(target)
    assert font_library_revision() == before + 1

    rejected = client.post(endpoint, files={"file": ("family.ttf", b"not a font", "font/ttf")})
    assert rejected.status_code == 400
    assert target.read_bytes() == payload
    assert client.get(endpoint).json() == [entry]
    assert not list(target.parent.glob(".upload-*"))


def test_project_font_upload_enforces_size_limit_without_publishing(client, asset, monkeypatch):
    from app.api import projects
    from app.storage import get_storage

    _, payload, _ = asset
    project = client.post("/api/projects", json={"name": "Bounded font upload"}).json()
    monkeypatch.setattr(projects, "get_settings", lambda: SimpleNamespace(max_upload_mb=0))
    endpoint = f"/api/projects/{project['id']}/fonts"
    response = client.post(endpoint, files={"file": ("family.ttf", payload, "font/ttf")})
    assert response.status_code == 413
    assert client.get(endpoint).json() == []
    assert not list((get_storage().project_dir(project["id"]) / "fonts").iterdir())


@pytest.fixture
def asset(tmp_path, monkeypatch):
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A"])
    glyphs = {}
    for name in [".notdef", "A"]:
        pen = TTGlyphPen(None)
        pen.moveTo((100, 0))
        pen.lineTo((300, 700))
        pen.lineTo((500, 0))
        pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics({name: (600, 0) for name in glyphs})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupCharacterMap({65: "A"})
    builder.setupNameTable({"familyName": "Preset Test Family", "styleName": "Regular",
                           "fullName": "Preset Test Family Regular", "psName": "PresetTestFamily-Regular"})
    # Mimic LXGW: its first name is a localized alias, not the English family
    # used by the pinned filename. Existing uploads must keep their old naming.
    builder.font["name"].names = [name for name in builder.font["name"].names if name.platformID == 3]
    builder.font["name"].setName("測試字體", 1, 3, 1, 1028)
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.setupMaxp()
    buffer = io.BytesIO()
    builder.save(buffer)
    payload = buffer.getvalue()
    preset = replace(font_presets.BY_ID["comic-neue"], family="Preset Test Family", size_bytes=len(payload),
                     sha256=hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(font_presets, "PRESETS", (preset,))
    monkeypatch.setattr(font_presets, "BY_ID", {preset.id: preset})
    monkeypatch.setattr(font_presets, "BY_FILENAME", {preset.filename: preset})
    from app.api import fonts

    directory = tmp_path / "fonts"
    directory.mkdir()
    monkeypatch.setattr(fonts, "_font_directory", lambda: directory)
    monkeypatch.setattr(fonts, "get_storage", lambda: StorageManager(tmp_path))
    return preset, payload, directory


@pytest.fixture
def transport(monkeypatch):
    clients = []
    calls = []

    def install(handler):
        def capture(request):
            calls.append(request)
            return handler(request)

        client = httpx.Client(transport=httpx.MockTransport(capture))
        clients.append(client)
        monkeypatch.setattr(font_presets.httpx, "stream", client.stream)
        return calls

    yield install
    for client in clients:
        client.close()


def test_reviewed_catalog_is_pinned_and_every_asset_has_its_own_license():
    assert len(font_presets.PRESETS) == 9
    assert len(font_presets.BY_FILENAME) == 9
    for preset in font_presets.PRESETS:
        assert preset.url.startswith("https://raw.githubusercontent.com/")
        assert "/main/" not in preset.url and "/latest/" not in preset.url
        assert len(preset.sha256) == 64 and 0 < preset.size_bytes < 32 * 1024**2
        license_text = (font_presets.LICENSE_DIRECTORY / f"{preset.id}.txt").read_text()
        assert "Copyright" in license_text and "SIL OPEN FONT LICENSE Version 1.1" in license_text


def test_catalog_never_downloads_fonts_and_unknown_ids_fail_closed(client, asset, transport):
    calls = transport(lambda request: pytest.fail("Catalog must be offline"))
    response = client.get("/api/fonts/presets")
    assert response.status_code == 200
    assert response.json()[0]["installed_filename"] is None
    assert "url" not in response.json()[0]
    assert client.post("/api/fonts/presets/not-a-font/install").status_code == 404
    assert not calls


def test_install_is_verified_idempotent_available_to_renderer_and_removable(client, asset, transport):
    preset, payload, directory = asset
    calls = transport(lambda request: httpx.Response(200, content=payload))
    response = client.post(f"/api/fonts/presets/{preset.id}/install")
    assert response.status_code == 201, response.text
    font = response.json()
    assert font["name"] == preset.family
    assert font["preset_id"] == preset.id
    assert (directory / font["filename"]).read_bytes() == payload
    from app.services.font_library import font_family

    assert font_family(directory / font["filename"]) == "測試字體"
    assert (directory / "licenses" / f"{preset.id}.txt").read_bytes() == (font_presets.LICENSE_DIRECTORY / f"{preset.id}.txt").read_bytes()
    assert client.get("/api/fonts").json()[0]["filename"] == font["filename"]
    assert client.get("/api/fonts/presets").json()[0]["installed_filename"] == font["filename"]
    resolver = FontResolver([directory])
    assert resolver.resolve(preset.family, character="A") == (directory / font["filename"]).resolve()
    assert client.post(f"/api/fonts/presets/{preset.id}/install").status_code == 201
    assert len(calls) == 1
    assert client.delete(f"/api/fonts/{font['filename']}").status_code == 204
    assert client.get("/api/fonts/presets").json()[0]["installed_filename"] is None
    assert client.post(f"/api/fonts/presets/{preset.id}/install").status_code == 201
    assert len(calls) == 2
    assert not list(directory.glob("*.part"))


@pytest.mark.parametrize("failure", ["truncated", "oversized", "checksum", "redirect", "timeout"])
def test_failed_download_leaves_no_font_or_partial_file_and_can_retry(client, asset, transport, failure):
    preset, payload, directory = asset

    def fail(request):
        if failure == "timeout":
            raise httpx.ReadTimeout("private upstream information")
        if failure == "redirect":
            return httpx.Response(302, headers={"Location": "http://127.0.0.1/private"})
        body = payload[:-1] if failure == "truncated" else payload + b"x" if failure == "oversized" else b"x" * len(payload)
        return httpx.Response(200, content=body)

    calls = transport(fail)
    response = client.post(f"/api/fonts/presets/{preset.id}/install")
    assert response.status_code == 502, response.text
    assert "private" not in response.text
    assert len(calls) == 1
    assert list(directory.iterdir()) == []
    assert client.get("/api/fonts").json() == []
    transport(lambda request: httpx.Response(200, content=payload))
    assert client.post(f"/api/fonts/presets/{preset.id}/install").status_code == 201


def test_existing_custom_file_is_never_overwritten(client, asset, transport):
    preset, _, directory = asset
    target = directory / preset.filename
    target.write_bytes(b"user-owned-file")
    calls = transport(lambda request: pytest.fail("Must not overwrite"))
    assert client.post(f"/api/fonts/presets/{preset.id}/install").status_code == 409
    assert target.read_bytes() == b"user-owned-file"
    assert not calls


def test_concurrent_duplicate_downloads_do_not_fetch_or_publish_twice(asset, transport):
    preset, payload, directory = asset
    started, release = Event(), Event()

    def handler(request):
        started.set()
        assert release.wait(5)
        return httpx.Response(200, content=payload)

    calls = transport(handler)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(font_presets.install, preset.id, directory)
        try:
            assert started.wait(5)
            with pytest.raises(font_presets.FontInstallError) as error:
                font_presets.install(preset.id, directory)
            assert error.value.status == 409
            assert not (directory / preset.filename).exists()
        finally:
            release.set()
        assert first.result() == directory / preset.filename
    assert len(calls) == 1
    assert not list(directory.glob("*.part"))


@pytest.mark.parametrize("family,first,second,expected", [
    ("LXGW WenKai", "LXGWWenKaiTC-Regular.ttf", "LXGWWenKai-Regular.ttf", "LXGWWenKai-Regular.ttf"),
    ("LXGW WenKai TC", "LXGWWenKai-Regular.ttf", "LXGWWenKaiTC-Regular.ttf", "LXGWWenKaiTC-Regular.ttf"),
    ("Noto Sans CJK SC", "NotoSansCJK-Bold.ttf", "NotoSansCJKSC-Regular.ttf", "NotoSansCJKSC-Regular.ttf"),
])
def test_renderer_prefers_the_requested_font_family_over_other_locales(tmp_path, family, first, second, expected):
    # Order deliberately puts the wrong family first. No glyph parsing needed:
    # this isolates the family/locale candidate ranking from FreeType behavior.
    for filename in (first, second):
        (tmp_path / filename).touch()
    resolver = FontResolver([tmp_path])
    resolver.paths = [tmp_path / first, tmp_path / second]
    assert resolver.resolve(family, weight=700) == tmp_path / expected


def test_install_refreshes_an_already_loaded_renderer_even_when_a_fallback_exists(client, asset, transport, monkeypatch):
    preset, payload, directory = asset
    preset = replace(preset, family=preset.family + " TC")
    monkeypatch.setattr(font_presets, "PRESETS", (preset,))
    monkeypatch.setattr(font_presets, "BY_ID", {preset.id: preset})
    monkeypatch.setattr(font_presets, "BY_FILENAME", {preset.filename: preset})
    fallback = directory / "PresetTestFamily-Regular.ttf"
    fallback.write_bytes(payload)
    resolver = FontResolver([directory])
    assert resolver.resolve(preset.family, character="A") == fallback.resolve()
    transport(lambda request: httpx.Response(200, content=payload))
    response = client.post(f"/api/fonts/presets/{preset.id}/install")
    assert response.status_code == 201, response.text
    assert resolver.resolve(preset.family, character="A") == (directory / preset.filename).resolve()

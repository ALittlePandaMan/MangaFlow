from io import BytesIO

from app.core.database import SessionLocal
from app.models import ImagePage
from app.storage import get_storage
from PIL import Image


def upload_page(client):
    project = client.post("/api/projects", json={"name": "preview-cache"}).json()
    image = BytesIO()
    Image.new("RGB", (1200, 1800), (180, 25, 20)).save(image, "PNG")
    original = image.getvalue()
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("page.png", original, "image/png")}).json()[0]
    return project, page, original


def test_previews_are_small_cached_and_leave_original_and_history_unchanged(client, monkeypatch):
    project, page, original = upload_page(client)
    history_url = f"/api/projects/{project['id']}/history"
    before = client.get(history_url).json()
    response = client.get(page["thumbnail_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
    with Image.open(BytesIO(response.content)) as preview:
        assert preview.size == (200, 300)
    with Image.open(BytesIO(client.get(page["thumbnail_2x_url"]).content)) as retina:
        assert retina.size == (400, 600)
    cover_url = client.get(f"/api/projects/{project['id']}").json()["cover_thumbnail_url"]
    with Image.open(BytesIO(client.get(cover_url).content)) as cover:
        assert cover.size == (768, 512)
    assert client.get(page["original_url"]).content == original
    assert client.get(history_url).json() == before

    def no_decode(*_args, **_kwargs):
        raise AssertionError("A cached thumbnail must not decode the source again")

    monkeypatch.setattr("app.services.image_previews.Image.open", no_decode)
    assert client.get(page["thumbnail_url"]).content == response.content
    assert client.get(cover_url).status_code == 200
    changed = client.patch(f"/api/images/{page['id']}/ocr-exempt?exempt=true").json()
    assert changed["thumbnail_url"] == page["thumbnail_url"]


def test_preview_invalidates_on_render_and_restore_but_cover_stays_original(client):
    project, page, original = upload_page(client)
    original_thumb = client.get(page["thumbnail_url"]).content
    cover_url = client.get(f"/api/projects/{project['id']}").json()["cover_thumbnail_url"]
    storage = get_storage()
    rendered_path = storage.page_dir(project["id"], page["id"]) / "rendered" / "page.png"
    Image.new("RGB", (1200, 1800), (20, 25, 180)).save(rendered_path)
    with SessionLocal() as db:
        model = db.get(ImagePage, page["id"])
        model.rendered_path = storage.relative(rendered_path)
        model.metadata_json = {"artifact_versions": {"rendered": 123}}
        db.commit()
    updated = client.get(f"/api/images/{page['id']}").json()
    assert updated["thumbnail_url"] != page["thumbnail_url"]
    new_thumb = client.get(updated["thumbnail_url"])
    with Image.open(BytesIO(new_thumb.content)) as image:
        assert image.getpixel((0, 0))[2] > 150
    assert client.get(page["thumbnail_url"]).headers["cache-control"] == "no-cache"
    assert client.get(f"/api/projects/{project['id']}").json()["cover_thumbnail_url"] == cover_url

    # Undo/import may point at another immutable file with the same timestamp.
    restored = storage.page_dir(project["id"], page["id"]) / "versions" / "restored.png"
    restored.write_bytes(original)
    with SessionLocal() as db:
        model = db.get(ImagePage, page["id"])
        model.rendered_path = storage.relative(restored)
        db.commit()
    restored_page = client.get(f"/api/images/{page['id']}").json()
    assert restored_page["thumbnail_url"] != updated["thumbnail_url"]
    assert client.get(restored_page["thumbnail_url"]).content == original_thumb


def test_preview_validates_variant_and_missing_page(client):
    _, page, _ = upload_page(client)
    assert client.get(f"/api/images/{page['id']}/preview/unbounded-size").status_code == 422
    assert client.get("/api/images/not-a-page/preview/page").status_code == 404


def test_palette_transparency_survives_thumbnail_encoding(client):
    project = client.post("/api/projects", json={"name": "transparent-preview"}).json()
    source = BytesIO()
    image = Image.new("P", (40, 60), 0)
    image.save(source, "PNG", transparency=0)
    page = client.post(f"/api/projects/{project['id']}/images", files={"files": ("transparent.png", source.getvalue(), "image/png")}).json()[0]
    with Image.open(BytesIO(client.get(page["thumbnail_url"]).content)) as preview:
        assert preview.getpixel((0, 0))[3] == 0

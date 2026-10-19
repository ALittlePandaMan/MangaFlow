from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from app.core.database import SessionLocal
from app.models import TextRegion
from app.services.layout.text_paint import BLACK, WHITE, auto_paint, source_paint, uses_auto_paint
from app.services.rendering.pillow_renderer import PillowRenderer
from app.services.rendering.snapshot import render_input
from PIL import Image, ImageDraw


def region(**values):
    return SimpleNamespace(layout_data={"text_paint": {"mode": "auto"}}, locked=False,
                           text_color="#111111", stroke_color=WHITE, stroke_width=0,
                           translated_polygon=None, polygon=None, **values)


@pytest.mark.parametrize("background,fill", [(BLACK, WHITE), (WHITE, BLACK), ("#888888", BLACK)])
def test_auto_contrast_on_solid_background(background, fill):
    style = auto_paint(Image.new("RGB", (100, 100), background), region(), 24, [10, 10, 60, 60])
    assert style["text_color"] == fill
    assert style["stroke_color"] == (WHITE if fill == BLACK else BLACK)
    assert style["stroke_width"] == 0


@pytest.mark.parametrize("fill,background", [(BLACK, WHITE), (WHITE, BLACK)])
def test_source_ink_samples_never_modify_or_dilate_mask(fill, background):
    image = Image.new("RGB", (90, 60), background)
    ImageDraw.Draw(image).rectangle((20, 20, 35, 40), fill=fill)
    mask = np.zeros((40, 40), dtype=np.uint8)
    mask[10:31, 10:26] = 255
    original = mask.copy()
    hint = source_paint(image, [10, 10, 40, 40], mask, (10, 10))
    assert hint["source_fill"] == fill
    np.testing.assert_array_equal(mask, original)


@pytest.mark.parametrize("mask", [None, np.zeros((30, 30), np.uint8), np.ones((30, 30), np.uint8)*255])
def test_empty_and_solid_masks_do_not_invent_source_polarity(mask):
    assert "source_fill" not in source_paint(Image.new("RGB", (30, 30), BLACK), [0, 0, 30, 30], mask, (0, 0))


def test_complex_background_gets_opposite_outline_and_polygon_limits_sampling():
    image = Image.new("RGB", (200, 100), BLACK)
    ImageDraw.Draw(image).rectangle((100, 0, 199, 99), fill=WHITE)
    style = auto_paint(image, region(), 32, [0, 0, 200, 100])
    assert style["stroke_width"] == 2
    assert style["text_color"] != style["stroke_color"]
    value = region()
    value.translated_polygon = [[5, 5], [70, 5], [70, 90], [5, 90]]
    limited = auto_paint(image, value, 32, [0, 0, 200, 100])
    assert limited["text_color"] == WHITE and limited["stroke_width"] == 0


def test_manual_paint_and_locked_regions_are_not_replaced():
    value = region()
    value.layout_data["text_paint"]["mode"] = "manual"
    assert not uses_auto_paint(value)
    value.layout_data["text_paint"]["mode"] = "auto"
    value.locked = True
    assert not uses_auto_paint(value)
    assert not uses_auto_paint(SimpleNamespace(layout_data={"detection": {"provider": "rfdetr"}},
        text_color="#ff0000", stroke_color=WHITE, stroke_width=0))


@pytest.mark.parametrize("background,fill", [(BLACK, WHITE), (WHITE, BLACK)])
def test_export_persists_auto_paint_and_user_override_is_authoritative(client, background, fill):
    data = BytesIO()
    Image.new("RGB", (220, 140), background).save(data, "PNG")
    project = client.post("/api/projects", json={"name": "paint test"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={
        "files": ("page.png", data.getvalue(), "image/png")}).json()[0]
    created = client.post(f"/api/images/{page['id']}/regions", json={
        "bbox": [10, 10, 160, 100], "translated_text": "TEXT"}).json()
    response = client.post(f"/api/projects/{project['id']}/export", json={"formats": ["translated"]})
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        saved = db.get(TextRegion, created["id"])
        assert saved.text_color == fill
        assert saved.layout_data["render_input"] == render_input(saved)
        assert saved.layout_data["text_paint"]["mode"] == "auto"
    patched = client.patch(f"/api/regions/{created['id']}", json={
        "text_color": "#123456", "stroke_color": "#654321", "stroke_width": 0})
    assert patched.status_code == 200, patched.text
    assert client.post(f"/api/projects/{project['id']}/export", json={"formats": ["translated"]}).status_code == 200
    with SessionLocal() as db:
        saved = db.get(TextRegion, created["id"])
        assert saved.text_color == "#123456" and saved.stroke_color == "#654321" and saved.stroke_width == 0
        assert saved.layout_data["text_paint"]["mode"] == "manual"
    from test_editor_history import apply as undo

    undo(client, project["id"])
    with SessionLocal() as db:
        saved = db.get(TextRegion, created["id"])
        assert saved.text_color == fill and uses_auto_paint(saved)
    undo(client, project["id"], "redo")
    with SessionLocal() as db:
        saved = db.get(TextRegion, created["id"])
        assert saved.text_color == "#123456" and not uses_auto_paint(saved)


def test_explicit_zero_outline_on_create_disables_auto_paint(client):
    data = BytesIO()
    Image.new("RGB", (100, 100), BLACK).save(data, "PNG")
    project = client.post("/api/projects", json={"name": "explicit paint"}).json()
    page = client.post(f"/api/projects/{project['id']}/images", files={
        "files": ("page.png", data.getvalue(), "image/png")}).json()[0]
    created = client.post(f"/api/images/{page['id']}/regions", json={
        "bbox": [5, 5, 70, 70], "translated_text": "TEXT", "stroke_width": 0}).json()
    with SessionLocal() as db:
        assert not uses_auto_paint(db.get(TextRegion, created["id"]))


def test_legacy_renderer_explicit_colors_still_respected():
    value = SimpleNamespace(text_color="#123456", stroke_color="#abcdef", stroke_width=0)
    style = PillowRenderer._resolve_render_style(Image.new("RGB", (100, 100), BLACK), value, 24, [0, 0, 90, 90])
    assert style["text_color"] == "#123456" and style["stroke_color"] == "#abcdef" and style["stroke_width"] == 0


def test_complex_auto_outline_and_font_do_not_drift_on_repeated_export(client):
    image = Image.new('RGB',(360,220),BLACK)
    ImageDraw.Draw(image).rectangle((180,0,359,219),fill=WHITE)
    data=BytesIO()
    image.save(data,'PNG')
    project=client.post('/api/projects',json={'name':'stable auto outline'}).json()
    page=client.post(f"/api/projects/{project['id']}/images",files={
        'files':('page.png',data.getvalue(),'image/png')}).json()[0]
    created=client.post(f"/api/images/{page['id']}/regions",json={
        'bbox':[10,10,330,190],'translated_text':'Large text'}).json()
    values=[]
    for _ in range(3):
        response=client.post(f"/api/projects/{project['id']}/export",json={'formats':['translated']})
        assert response.status_code == 200,response.text
        with SessionLocal() as db:
            value=db.get(TextRegion,created['id'])
            values.append((value.font_size,value.text_color,value.stroke_color,value.stroke_width))
            assert value.layout_data['render_input']==render_input(value)
    assert values[0]==values[1]==values[2]

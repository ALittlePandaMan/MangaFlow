from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app.services.layout.engine import FontResolver, MangaLayoutEngine
from app.services.layout.font_sizing import automatic_font_size
from app.services.rendering import PillowRenderer
from app.services.rendering.snapshot import render_input
from test_layout import available_font, simple_render_region


def layout(text="漫画翻译", box=(100, 150), **kwargs):
    font = available_font()
    return MangaLayoutEngine(FontResolver([font.parent])).layout(text, [0,0,*box],
        orientation=kwargs.pop("orientation", "vertical"), font_family=font.stem,
        preferred_size=kwargs.pop("preferred_size", 12), line_spacing=1.15,
        character_spacing=kwargs.pop("character_spacing", 0), alignment="center",
        custom_font_path=str(font), **kwargs)


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_initial_font_grows_to_largest_fitting_size_not_previous_cap(orientation):
    result = layout("漫画翻译", box=(200,200), orientation=orientation)
    assert result.font_size > 12 and not result.overflow
    # The next integer size must not fit the same frame.
    next_size = layout("漫画翻译", box=(196,196), orientation=orientation,
        preferred_size=result.font_size+1, auto_fit=False)
    assert next_size.overflow
    small = layout("漫画翻译", box=(40,50), orientation=orientation)
    assert small.font_size < result.font_size and not small.overflow


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
@pytest.mark.parametrize("size", [6, 120])
def test_user_font_adjustment_is_never_replaced_by_frame_fit(orientation,size):
    result = layout(box=(20,30),orientation=orientation,preferred_size=size,auto_fit=False)
    assert result.font_size == size


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
@pytest.mark.parametrize("perspective", [False, True])
@pytest.mark.parametrize("rotation", [0, 30])
def test_user_sized_export_preserves_overflow_pixels(tmp_path,orientation,perspective,rotation):
    region = simple_render_region()
    region.translated_text = "字号"
    region.bbox = [120,100,30,40]
    region.polygon = [[120,100],[150,100],[150,140],[120,140]]
    region.translated_bbox,region.translated_polygon = region.bbox,region.polygon
    region.font_size = 80
    region.layout_data["auto_font_size"] = False
    region.orientation,region.perspective_warp,region.rotation = orientation,perspective,rotation
    bg,out,layer = [tmp_path/name for name in ("bg.png","out.png","text.png")]
    Image.new("RGB",(320,300),"white").save(bg)
    result = PillowRenderer().render(bg,[region],out,layer,shape_masks={region.id:np.zeros((300,320),np.uint8)})
    saved = result["layouts"][region.id]
    assert saved["font_size"] == 80 and saved["auto_font_size"] is False
    assert not saved["shape_layout_applied"] and not saved["render_skipped"]
    with Image.open(layer) as image:
        bounds = image.getbbox()
        assert bounds is not None
        assert bounds[0] < 120 or bounds[1] < 100 or bounds[2] > 150 or bounds[3] > 140
        composite = Image.alpha_composite(Image.open(bg).convert("RGBA"),image).convert("RGB")
    np.testing.assert_array_equal(np.asarray(composite),np.asarray(Image.open(out)))
    assert result["inputs"][region.id]["auto_font_size"] is False


def test_initial_font_accounts_for_stroke_and_does_not_overflow(tmp_path):
    region = simple_render_region()
    region.stroke_width = 8
    bg,out,layer = [tmp_path/name for name in ("bg.png","out.png","text.png")]
    Image.new("RGB",(240,100),"white").save(bg)
    result = PillowRenderer().render(bg,[region],out,layer)
    assert result["layouts"][region.id]["auto_font_size"] is True
    bounds = Image.open(layer).getbbox()
    assert bounds and 10 <= bounds[0] < bounds[2] <= 210 and 10 <= bounds[1] < bounds[3] <= 90


def test_initial_fit_keeps_rotated_text_inside_without_clipping():
    normal=layout("漫画",box=(100,100),orientation="horizontal")
    rotated=layout("漫画",box=(100,100),orientation="horizontal",rotation=45)
    assert not rotated.overflow and rotated.font_size < normal.font_size


def test_font_override_detection_for_legacy_and_hidden_regions():
    assert automatic_font_size({},24)
    assert not automatic_font_size({"render_input":{"font_size":20}},60)
    assert automatic_font_size({"auto_font_size":True,"font_size":20},60)
    assert not automatic_font_size({"auto_font_size":False},20)
    assert render_input(SimpleNamespace(layout_data={}))['auto_font_size'] is True


def test_font_edits_persist_without_a_mode_control_and_survive_resize_copy_restore(client):
    project = client.post("/api/projects",json={"name":"font-sizing-test"}).json()
    buffer = BytesIO()
    Image.new("RGB",(300,300),"white").save(buffer,"PNG")
    page = client.post(f"/api/projects/{project['id']}/images",files={"files":("page.png",buffer.getvalue(),"image/png")}).json()[0]
    region = client.post(f"/api/images/{page['id']}/regions",json={"bbox":[30,30,100,150],"translated_text":"漫画"}).json()
    assert region["auto_font_size"] is True
    changed = client.patch(f"/api/regions/{region['id']}",json={"font_size":120}).json()
    assert changed["font_size"] == 120 and changed["auto_font_size"] is False
    resized = client.patch(f"/api/regions/{region['id']}",json={"translated_bbox":[30,30,20,20]}).json()
    assert resized["font_size"] == 120 and resized["auto_font_size"] is False
    copied = client.post(f"/api/regions/{region['id']}/copy").json()
    assert copied["font_size"] == 120 and copied["auto_font_size"] is False
    revisions = client.get(f"/api/regions/{region['id']}/revisions").json()
    original = next(item for item in revisions if item["snapshot"]["layout_data"].get("auto_font_size") is True)
    restored = client.post(f"/api/regions/{region['id']}/revisions/{original['id']}/restore").json()
    assert restored["auto_font_size"] is True

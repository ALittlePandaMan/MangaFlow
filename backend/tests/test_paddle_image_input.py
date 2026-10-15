from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace

import numpy as np
import pytest
from app.services.base import ProviderError
from app.services.infra.paddle import load_paddle_image, predict_paddle_image
from PIL import Image

POLYGON = [[1, 2], [12, 2], [12, 18], [1, 18]]


@pytest.mark.parametrize("extension,format", [(".jpg","JPEG"),(".jpeg","JPEG"),(".png","PNG"),(".webp","WEBP")])
def test_every_accepted_upload_format_reaches_detection_as_pixels(tmp_path, extension, format):
    path = tmp_path / f"漫画-日本語{extension}"
    Image.new("RGB", (20, 30), (240, 90, 20)).save(path, format=format)
    before = sha256(path.read_bytes()).hexdigest()
    calls = []
    class Model:
        def predict(self, image):
            calls.append(image)
            yield {"dt_polys": [POLYGON], "dt_scores": [.95]}
    result = predict_paddle_image(Model(), load_paddle_image(path))
    assert result[0]['dt_polys'] == [POLYGON]
    image = calls[0]
    assert image.shape == (30,20,3) and image.dtype == np.uint8 and image.flags.c_contiguous
    np.testing.assert_allclose(image[10,10], (20,90,240), atol=2)
    assert sha256(path.read_bytes()).hexdigest() == before
    assert list(tmp_path.iterdir()) == [path]  # no temporary conversion files


@pytest.mark.parametrize("mode", ["L","RGBA","P"])
def test_grayscale_and_transparency_are_normalized_for_detection(tmp_path,mode):
    path=tmp_path/'page.png'
    if mode == 'L':
        source=Image.new(mode,(20,30),110)
    elif mode == 'RGBA':
        source=Image.new(mode,(20,30),(0,0,0,0))
        source.putpixel((2,3),(10,20,30,255))
    else:
        source=Image.new(mode,(20,30),0)
        source.putpalette([0,0,0,10,20,30]+[0]*762)
        source.putpixel((2,3),1)
        source.info['transparency']=0
    source.save(path)
    image=load_paddle_image(path)
    assert image.shape == (30,20,3) and image.flags.c_contiguous
    np.testing.assert_array_equal(image[0,0], [110]*3 if mode=='L' else [255]*3)
    if mode != 'L':
        np.testing.assert_array_equal(image[3,2],[30,20,10])


def test_transparent_webp_uses_white_background_without_changing_original(tmp_path):
    path=tmp_path/'page.webp'
    Image.new('RGBA',(20,30),(0,0,0,0)).save(path,'WEBP',lossless=True)
    before=path.read_bytes()
    np.testing.assert_array_equal(load_paddle_image(path),np.full((30,20,3),255,np.uint8))
    assert path.read_bytes() == before


@pytest.mark.parametrize('missing',[False,True])
def test_decode_failure_is_an_error_and_does_not_invoke_model(tmp_path,missing):
    path=tmp_path/'bad.webp'
    if not missing:
        path.write_bytes(b'not-an-image')
    class Model:
        def predict(self,_):
            raise AssertionError('invalid files must fail before inference')
    with pytest.raises(ProviderError,match='Cannot decode image'):
        predict_paddle_image(Model(), load_paddle_image(path))


@pytest.mark.parametrize('returned',[None, [], iter(()), [{}], [{'error':'unsupported format'}]])
def test_missing_page_result_cannot_be_reported_as_success(returned):
    model=SimpleNamespace(predict=lambda _:returned)
    with pytest.raises(ProviderError,match='image input was not processed|no page result'):
        predict_paddle_image(model,np.zeros((30,20,3),np.uint8))


def test_blank_page_is_a_valid_zero_detection_not_an_input_error(tmp_path):
    path=tmp_path/'blank.webp'
    Image.new('RGB',(20,30),'white').save(path,'WEBP')
    model=SimpleNamespace(predict=lambda _:[{'dt_polys':[], 'dt_scores':[]}])
    assert predict_paddle_image(model, load_paddle_image(path))[0]['dt_polys'] == []

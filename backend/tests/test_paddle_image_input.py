from __future__ import annotations

import sys
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from app.services.base import ProviderError, ProviderState
from app.services.detection.providers import PaddleTextDetector
from app.services.infra.paddle import load_paddle_image, predict_paddle_image
from PIL import Image

POLYGON = [[1, 2], [12, 2], [12, 18], [1, 18]]


def ready_detector(model):
    detector = PaddleTextDetector({"bubble_grouping": False})
    detector._model = model
    detector.state = ProviderState.READY
    return detector


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
    result = ready_detector(Model()).detect(path)
    assert len(result) == 1 and result[0].bbox == [1.,2.,11.,16.]
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
        ready_detector(Model()).detect(path)


@pytest.mark.parametrize('returned',[None, [], iter(()), [{}], [{'error':'unsupported format'}]])
def test_missing_page_result_cannot_be_reported_as_success(returned):
    model=SimpleNamespace(predict=lambda _:returned)
    with pytest.raises(ProviderError,match='image input was not processed|no page result'):
        predict_paddle_image(model,np.zeros((30,20,3),np.uint8))


def test_blank_page_is_a_valid_zero_detection_not_an_input_error(tmp_path):
    path=tmp_path/'blank.webp'
    Image.new('RGB',(20,30),'white').save(path,'WEBP')
    model=SimpleNamespace(predict=lambda _:[{'dt_polys':[], 'dt_scores':[]}])
    assert ready_detector(model).detect(path) == []


def test_lazy_gpu_failure_retries_decoded_pixels_on_cpu(tmp_path,monkeypatch):
    path=tmp_path/'page.webp'
    Image.new('RGB',(20,30),'white').save(path,'WEBP')
    class FailingGPU:
        def predict(self,image):
            assert isinstance(image,np.ndarray)
            raise RuntimeError('CUDA out of memory')
            yield  # failure occurs while consuming the iterator, not predict()
    inputs=[]
    class CPU:
        def predict(self,image):
            inputs.append(image)
            return [{'dt_polys':[POLYGON],'dt_scores':[.9]}]
    detector=ready_detector(FailingGPU())
    detector._device='gpu:0'
    monkeypatch.setattr('app.services.detection.providers.release_paddle_cuda',lambda:None)
    monkeypatch.setitem(sys.modules,'paddleocr',SimpleNamespace(PaddleOCR=object))
    monkeypatch.setattr(detector,'_create_model',lambda _class,device:CPU() if device=='cpu' else None)
    assert len(detector.detect(path)) == 1
    assert detector._device == 'cpu' and len(inputs) == 1


def test_empty_model_response_fails_task_without_erasing_existing_frames(client,monkeypatch):
    from app.pipeline.processor import PipelineProcessor
    from test_pipeline import wait_for_task
    image=BytesIO()
    Image.new('RGB',(200,200),'white').save(image,'WEBP')
    project=client.post('/api/projects',json={'name':'WebP input failure safety'}).json()
    page=client.post(f"/api/projects/{project['id']}/images",files={'files':('page.webp',image.getvalue(),'image/webp')}).json()[0]
    region=client.post(f"/api/images/{page['id']}/regions",json={'bbox':[20,20,60,120],'source_text':'preserve me'}).json()
    detector=ready_detector(SimpleNamespace(predict=lambda _:iter(())))
    original=PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor,'_provider',lambda self,kind,name:(detector,'test-paddle') if kind=='detection' else original(self,kind,name))
    requested=client.post(f"/api/images/{page['id']}/process",json={'start_stage':'detection','end_stage':'detection','force':True}).json()
    finished=wait_for_task(client,requested['id'])
    assert finished['status'] == 'FAILED'
    assert 'no page result' in finished['error_message']
    retained=client.get(f"/api/images/{page['id']}/regions").json()
    assert len(retained) == 1 and retained[0]['id'] == region['id'] and retained[0]['source_text'] == 'preserve me'

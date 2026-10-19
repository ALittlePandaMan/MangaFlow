import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest
from app.services.base import ProviderState
from app.services.execution import execution_runtime
from app.services.inpainting import providers
from PIL import Image


@pytest.mark.parametrize('alias', [False, True])
@pytest.mark.parametrize('phase', ['decode', 'encode'])
def test_lama_io_does_not_hold_global_model_slot_but_inference_does(tmp_path, monkeypatch, alias, phase):
    source, mask, output = [tmp_path/name for name in ('original.png', 'mask.png', 'clean.png')]
    Image.new('RGB', (80, 100), 'white').save(source)
    pixels = np.zeros((100,80), np.uint8)
    pixels[20:30,20:30] = 255
    Image.fromarray(pixels).save(mask)
    runtime = execution_runtime()
    model = providers.HybridInpainter() if alias else providers.SimpleLaMaInpainter()
    actual = model._lama if alias else model
    actual.state = ProviderState.READY
    actual._model = lambda image, mask: image if runtime.model_active == 1 else pytest.fail('unlocked inference')
    entered, release = threading.Event(), threading.Event()
    original = providers.load_rgb_with_metadata if phase == 'decode' else providers.save_png_with_metadata

    def io(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(providers, 'load_rgb_with_metadata' if phase == 'decode' else 'save_png_with_metadata', io)
    with ThreadPoolExecutor(2) as pool:
        future = pool.submit(model.inpaint, source, mask, output, 'background_complex')
        try:
            assert entered.wait(3)

            def other_model():
                with runtime.model_slot():
                    return True

            assert pool.submit(other_model).result(2), 'CPU IO blocked other model inference'
        finally:
            release.set()
        future.result(5)


def test_shared_lama_is_serialized_and_can_reload_after_unload_between_jobs(tmp_path):
    source, mask = [tmp_path/name for name in ('original.png','mask.png')]
    Image.new('RGB',(80,100),'white').save(source)
    Image.new('L',(80,100),255).save(mask)
    model = providers.SimpleLaMaInpainter()
    model.state = ProviderState.READY
    active, peak = 0, 0

    def infer(image, mask):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        threading.Event().wait(.015)
        active -= 1
        return image

    model._model = infer
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda i: model.inpaint(source,mask,tmp_path/f'{i}.png','background_complex'),range(8)))
    assert peak == 1

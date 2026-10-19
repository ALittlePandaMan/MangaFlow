from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from app.services.inpainting import text_segmentation as module
from PIL import Image


@pytest.fixture(autouse=True)
def isolated_source_cache():
    with module._cache_lock:
        module._sources.clear()
        module._source_bytes = 0
    yield
    with module._cache_lock:
        module._sources.clear()
        module._source_bytes = 0
    assert not module._flights


def setup(tmp_path, monkeypatch, infer):
    model = tmp_path / module.MODEL_FILENAME
    model.parent.mkdir()
    model.write_bytes(b"mock weights")
    source = tmp_path / "source.png"
    Image.new("RGB", (60, 40), "white").save(source)
    monkeypatch.setattr(module, "_segmenter", lambda *args: SimpleNamespace(predict=infer))
    return source


def test_local_windows_decode_and_hash_original_once_and_keep_legacy_cache_keys(tmp_path, monkeypatch):
    source = setup(tmp_path, monkeypatch, lambda image: np.full(image.shape[:2], .7, np.float32))
    calls = []
    original = Path.read_bytes

    def read(path):
        if path == source:
            calls.append(1)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    cache = tmp_path / "cache"
    for window in [None, (0, 0, 20, 20), (30, 10, 20, 20), None]:
        module.text_probability(source, tmp_path, cache, window=window)
        key = hashlib.sha256(original(source) + module.MODEL_SHA256.encode() + str(window).encode()).hexdigest()
        assert (cache / f"{key}.png").is_file()
    assert calls == [1]


def test_same_cache_miss_is_inferred_once_across_threads(tmp_path, monkeypatch):
    calls = []
    entered, release = threading.Event(), threading.Event()

    def infer(image):
        calls.append(1)
        assert not image.flags.writeable
        entered.set()
        assert release.wait(3)
        return np.full(image.shape[:2], .7, np.float32)

    source = setup(tmp_path, monkeypatch, infer)
    cache = tmp_path / "cache"
    with ThreadPoolExecutor(4) as pool:
        jobs = [pool.submit(module.text_probability, source, tmp_path, cache) for _ in range(4)]
        assert entered.wait(3)
        release.set()
        results = [job.result(3) for job in jobs]
    assert calls == [1]
    assert sum(not metadata["cached"] for _, metadata in results) == 1
    assert all(np.allclose(probability, .7, atol=1 / 65535) for probability, _ in results)


def test_source_cache_is_byte_bounded_and_invalidated_on_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(module, "SOURCE_CACHE_BYTES", 60 * 40 * 3)
    paths = [tmp_path / f"source-{index}.png" for index in range(3)]
    for index, path in enumerate(paths):
        Image.new("RGB", (60, 40), (index * 70, 0, 0)).save(path)
        pixels, _ = module._source_image(path)
        assert pixels[0, 0, 2] == index * 70
        assert module._source_bytes <= module.SOURCE_CACHE_BYTES
    assert len(module._sources) == 1
    Image.new("RGB", (60, 40), "white").save(paths[-1])
    pixels, _ = module._source_image(paths[-1])
    assert np.all(pixels == 255)
    assert len(module._sources) == 1
    assert np.array_equal(pixels, cv2.imread(str(paths[-1])))


def test_failure_does_not_poison_single_flight_or_leave_cache_files(tmp_path, monkeypatch):
    def fail(image):
        raise RuntimeError("inference failed")

    source = setup(tmp_path, monkeypatch, fail)
    cache = tmp_path / "cache"
    with pytest.raises(RuntimeError, match="inference failed"):
        module.text_probability(source, tmp_path, cache)
    assert not module._flights
    assert not cache.exists()
    monkeypatch.setattr(module, "_segmenter", lambda *args: SimpleNamespace(
        predict=lambda image: np.ones(image.shape[:2], np.float32),
    ))
    probability, _ = module.text_probability(source, tmp_path, cache)
    assert np.all(probability == 1)


def test_source_mutation_during_read_is_rejected(tmp_path, monkeypatch):
    source = tmp_path / "source.png"
    Image.new("RGB", (60, 40), "white").save(source)
    read = Path.read_bytes

    def changing_read(path):
        data = read(path)
        Image.new("RGB", (60, 40), "black").save(source)
        return data

    monkeypatch.setattr(Path, "read_bytes", changing_read)
    with pytest.raises(ValueError, match="changed while reading"):
        module._source_image(source)
    assert not module._sources and not module._flights


def test_cold_model_initialization_is_coalesced_across_pages(tmp_path, monkeypatch):
    model = tmp_path / module.MODEL_FILENAME
    model.parent.mkdir()
    model.write_bytes(b"mock weights")
    sources = [tmp_path / f"page-{index}.png" for index in range(4)]
    for index, source in enumerate(sources):
        Image.new("RGB", (60, 40), (index * 40, 0, 0)).save(source)
    calls = []

    class Segmenter:
        def __init__(self, path, **kwargs):
            calls.append(1)
            time.sleep(.05)

        def predict(self, image):
            return np.ones(image.shape[:2], np.float32)

    module._segmenter.cache_clear()
    monkeypatch.setattr(module, "ComicTextSegmenter", Segmenter)
    try:
        with ThreadPoolExecutor(4) as pool:
            jobs = [pool.submit(module.text_probability, source, tmp_path) for source in sources]
            for job in jobs:
                assert np.all(job.result(3)[0] == 1)
        assert calls == [1]
    finally:
        module._segmenter.cache_clear()

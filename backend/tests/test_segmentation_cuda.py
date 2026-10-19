from __future__ import annotations

import hashlib
from contextlib import nullcontext
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from app.core.config import get_settings
from app.services.execution import execution_runtime
from app.services.inpainting import segmentation_cuda
from app.services.inpainting import text_segmentation as module


def mock_model(tmp_path, monkeypatch):
    path = tmp_path / "model.onnx"
    path.write_bytes(b"test weights")
    monkeypatch.setattr(module, "MODEL_SHA256", hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(get_settings(), "segmentation_backend", "onnx_cuda")
    monkeypatch.setattr(execution_runtime(), "model_slot", nullcontext)
    return path


@pytest.mark.parametrize("backend", ["auto", "onnx_cuda"])
def test_cuda_uses_identical_rgb_fp32_padding_and_restores_original_dimensions(tmp_path, monkeypatch, backend):
    path = mock_model(tmp_path,monkeypatch)
    seen = []

    class Session:
        def __init__(self,*args):
            pass

        def forward(self,blob):
            seen.append(blob)
            return [np.full((1,1,1024,1024),.7,np.float32), np.zeros((1,2,8,8),np.float32)]

    monkeypatch.setattr(segmentation_cuda,"CudaSegmentationSession",Session)
    # Loading a successful CUDA backend must not also allocate a CPU network.
    monkeypatch.setattr(cv2.dnn,"readNetFromONNX",lambda *args: pytest.fail("Unexpected CPU network"))
    segmenter = module.ComicTextSegmenter(path, backend=backend)
    image = np.full((50,100,3),(10,20,30),np.uint8)
    result = segmenter.predict(image)
    assert result.shape == image.shape[:2] and np.allclose(result,.7)
    blob, = seen
    assert blob.dtype == np.float32 and blob.shape == (1,3,1024,1024)
    assert np.allclose(blob[0,:,0,0],np.array([30,20,10])/255)
    assert not np.any(blob[:,:,512:])


@pytest.mark.parametrize("during_inference",[False,True])
@pytest.mark.parametrize("backend", ["auto", "onnx_cuda"])
def test_unavailable_or_failed_cuda_falls_back_once_and_keeps_valid_probability(tmp_path,monkeypatch,during_inference,backend):
    path = mock_model(tmp_path,monkeypatch)
    attempts, cpu_calls = [], []

    class Session:
        def __init__(self,*args):
            attempts.append("load")
            if not during_inference:
                raise RuntimeError("driver unavailable")

        def forward(self,blob):
            attempts.append("run")
            raise RuntimeError("out of memory")

    net = SimpleNamespace(setInput=lambda blob: cpu_calls.append(blob),getUnconnectedOutLayersNames=lambda: ["mask"],
                          forward=lambda names: [np.full((1,1,1024,1024),.8,np.float32)])
    monkeypatch.setattr(segmentation_cuda,"CudaSegmentationSession",Session)
    monkeypatch.setattr(cv2.dnn,"readNetFromONNX",lambda *args: net)
    segmenter = module.ComicTextSegmenter(path, backend=backend)
    for _ in range(2):
        assert np.allclose(segmenter.predict(np.zeros((50,100,3),np.uint8)),.8)
    assert segmenter.backend == "opencv" and segmenter.cuda is None
    assert segmenter.fallback_reason == ("cuda_inference_failed" if during_inference else "cuda_initialization_failed")
    diagnostic = next(row for row in execution_runtime().snapshot()["inference"] if row["kind"]=="segmentation")
    assert diagnostic["device"] == "cpu" and diagnostic["backend"] == "opencv"
    assert diagnostic["fallback_reason"] == segmenter.fallback_reason
    assert attempts == (["load","run"] if during_inference else ["load"])
    assert len(cpu_calls) == 2


def test_explicit_cpu_segmentation_does_not_probe_or_load_cuda(tmp_path, monkeypatch):
    path = mock_model(tmp_path, monkeypatch)
    monkeypatch.setattr(segmentation_cuda, "CudaSegmentationSession", lambda *args: pytest.fail("CPU selection must stay CPU"))
    assert module.ComicTextSegmenter(path, backend="opencv").backend == "opencv"


def test_fallback_map_is_not_written_under_cuda_cache_identity(tmp_path):
    class Fallback:
        backend = "onnx_cuda"
        fallback_reason = None

        def predict(self,image):
            self.backend,self.fallback_reason = "opencv","cuda_inference_failed"
            return np.full(image.shape[:2],.8,np.float32)

    target = tmp_path / "cuda-map.png"
    probability,metadata = module._cached_probability(Fallback(),np.zeros((50,100,3),np.uint8),target)
    assert not target.exists() and np.allclose(probability,.8)
    assert metadata["backend"] == "opencv" and metadata["fallback_reason"] == "cuda_inference_failed"


def test_invalid_cuda_probability_uses_valid_cpu_output_instead_of_disabling_segmentation(tmp_path,monkeypatch):
    path = mock_model(tmp_path,monkeypatch)
    monkeypatch.setattr(segmentation_cuda,"CudaSegmentationSession",lambda *args: SimpleNamespace(
        forward=lambda blob: [np.full((1,1,1024,1024),np.nan,np.float32)]))
    net = SimpleNamespace(setInput=lambda blob: None,getUnconnectedOutLayersNames=lambda: ["mask"],
                          forward=lambda names: [np.full((1,1,1024,1024),.8,np.float32)])
    monkeypatch.setattr(cv2.dnn,"readNetFromONNX",lambda *args: net)
    segmenter = module.ComicTextSegmenter(path)
    assert np.allclose(segmenter.predict(np.zeros((50,100,3),np.uint8)),.8)
    assert segmenter.backend == "opencv" and segmenter.fallback_reason == "cuda_inference_failed"


@pytest.mark.parametrize("free,total,expected",[(3,8,True),(2,8,False),(3,16,True),(2.8,16,False),(5,32,False),(6,32,True)])
def test_cuda_keeps_two_gib_or_fifteen_percent_reserve(free,total,expected):
    torch = SimpleNamespace(cuda=SimpleNamespace(mem_get_info=lambda device: (int(free*1024**3),int(total*1024**3))))
    assert segmentation_cuda.memory_headroom(torch) is expected


def test_increasing_gpu_reserve_reduces_ctd_admission_without_removing_the_safety_floor(monkeypatch):
    monkeypatch.setattr(execution_runtime().policy,"gpu_memory_reserve_mb",4096)
    torch = SimpleNamespace(cuda=SimpleNamespace(mem_get_info=lambda device: (4*1024**3,16*1024**3)))
    assert not segmentation_cuda.memory_headroom(torch)

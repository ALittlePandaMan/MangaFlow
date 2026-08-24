from __future__ import annotations

import sys
from types import SimpleNamespace

from app.services.infra import device


def test_auto_uses_gpu_for_torch_when_available(monkeypatch) -> None:
    monkeypatch.setattr(device, "torch_cuda_available", lambda: True)
    assert device.resolve_torch_device("auto") == "cuda:0"
    assert device.resolve_torch_device("cuda:2") == "cuda:2"


def test_torch_falls_back_to_cpu_when_cuda_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(device, "torch_cuda_available", lambda: False)
    assert device.resolve_torch_device("auto") == "cpu"
    assert device.resolve_torch_device("cuda:0") == "cpu"


def test_auto_uses_paddle_gpu_notation_when_available(monkeypatch) -> None:
    monkeypatch.setattr(device, "paddle_cuda_available", lambda: True)
    assert device.resolve_paddle_device("auto") == "gpu:0"
    assert device.resolve_paddle_device("cuda:2") == "gpu:2"
    assert device.resolve_paddle_device("gpu:1") == "gpu:1"


def test_paddle_falls_back_to_cpu_when_gpu_is_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(device, "paddle_cuda_available", lambda: False)
    assert device.resolve_paddle_device("auto") == "cpu"
    assert device.resolve_paddle_device("gpu:0") == "cpu"


def test_paddle_probe_does_not_break_setup_when_runtime_is_partially_initialized(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "paddle", SimpleNamespace())

    assert device.paddle_cuda_available() is False


def test_accelerator_error_detects_nested_cuda_failure() -> None:
    try:
        try:
            raise RuntimeError("CUDA out of memory")
        except RuntimeError as exc:
            raise ValueError("model inference failed") from exc
    except ValueError as exc:
        assert device.is_accelerator_error(exc) is True

    assert device.is_accelerator_error(ValueError("invalid image path")) is False

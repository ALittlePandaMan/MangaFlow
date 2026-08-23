from __future__ import annotations

import gc
import logging

logger = logging.getLogger(__name__)


def torch_cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except (ImportError, RuntimeError):
        return False


def paddle_cuda_available() -> bool:
    try:
        import paddle

        return bool(paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0)
    except (ImportError, RuntimeError):
        return False


def resolve_torch_device(requested: object = "auto") -> str:
    """Resolve an automatic/CUDA request to a usable PyTorch device."""
    normalized = str(requested or "auto").strip().lower()
    wants_cuda = normalized in {"auto", "gpu", "cuda"} or normalized.startswith("cuda:")
    if not wants_cuda:
        return normalized
    if torch_cuda_available():
        return normalized if normalized.startswith("cuda:") else "cuda:0"
    if normalized != "auto":
        logger.warning("CUDA was requested for a PyTorch provider but is unavailable; using CPU")
    return "cpu"


def resolve_paddle_device(requested: object = "auto") -> str:
    """Resolve common device names to Paddle's cpu/gpu:N notation."""
    normalized = str(requested or "auto").strip().lower()
    wants_cuda = (
        normalized in {"auto", "gpu", "cuda"}
        or normalized.startswith("gpu:")
        or normalized.startswith("cuda:")
    )
    if not wants_cuda:
        return normalized
    if paddle_cuda_available():
        if normalized.startswith(("gpu:", "cuda:")):
            return f"gpu:{normalized.split(':', 1)[1]}"
        return "gpu:0"
    if normalized != "auto":
        logger.warning("GPU was requested for a Paddle provider but is unavailable; using CPU")
    return "cpu"


def is_accelerator_error(exc: BaseException) -> bool:
    """Identify CUDA/GPU runtime failures that are safe to retry on CPU."""
    messages: list[str] = []
    current: BaseException | None = exc
    while current is not None:
        messages.append(str(current).lower())
        current = current.__cause__ or current.__context__
    combined = " ".join(messages)
    return any(
        marker in combined
        for marker in (
            "cuda",
            "cudnn",
            "cublas",
            "nccl",
            "gpu",
            "out of memory",
            "memory allocation",
        )
    )


def release_torch_cuda() -> None:
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except (ImportError, RuntimeError):
        pass


def release_paddle_cuda() -> None:
    gc.collect()
    try:
        import paddle

        if paddle.device.is_compiled_with_cuda():
            paddle.device.cuda.empty_cache()
    except (ImportError, RuntimeError):
        pass

from __future__ import annotations

import gc
import importlib.util
import logging
import os
import platform
from typing import Any

logger = logging.getLogger(__name__)


def _system_memory_gb() -> float | None:
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return round(int(line.split()[1]) / 1024 / 1024, 1)
    except (OSError, ValueError, IndexError):
        return None
    return None


def device_profile() -> dict[str, Any]:
    """Return a safe hardware/runtime summary used by the setup wizard."""
    torch_report: dict[str, Any] = {
        "installed": importlib.util.find_spec("torch") is not None,
        "cuda_available": False,
    }
    paddle_report: dict[str, Any] = {
        "installed": importlib.util.find_spec("paddle") is not None,
        "cuda_available": False,
    }
    gpu_devices: list[dict[str, Any]] = []

    if torch_report["installed"]:
        try:
            import torch

            torch_report.update(
                {
                    "version": str(torch.__version__),
                    "cuda_version": str(torch.version.cuda or ""),
                    "cuda_available": bool(torch.cuda.is_available()),
                }
            )
            if torch_report["cuda_available"]:
                for index in range(torch.cuda.device_count()):
                    properties = torch.cuda.get_device_properties(index)
                    gpu_devices.append(
                        {
                            "index": index,
                            "name": properties.name,
                            "memory_gb": round(properties.total_memory / 1024**3, 1),
                            "compute_capability": f"{properties.major}.{properties.minor}",
                        }
                    )
        except Exception as exc:  # Hardware probing must return a report instead of breaking settings.
            torch_report["error"] = str(exc)

    if paddle_report["installed"]:
        try:
            import paddle

            paddle_cuda = bool(
                paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0
            )
            paddle_report.update(
                {
                    "version": str(paddle.__version__),
                    "cuda_available": paddle_cuda,
                }
            )
        except Exception as exc:  # Paddle can raise driver-specific exceptions during probing.
            paddle_report["error"] = str(exc)

    memory_gb = _system_memory_gb()
    torch_cuda = bool(torch_report["cuda_available"])
    paddle_cuda = bool(paddle_report["cuda_available"])
    has_gpu = torch_cuda or paddle_cuda
    profile = "gpu" if has_gpu else "cpu"
    provider_devices = {
        "detection": "cuda:0" if paddle_cuda else "cpu",
        "ocr": "cuda:0" if torch_cuda else "cpu",
        "inpainting": "cuda:0" if torch_cuda else "cpu",
        "rendering": "cpu",
        "translation": "remote",
    }
    return {
        "platform": {
            "system": platform.system(),
            "architecture": platform.machine(),
        },
        "cpu": {
            "logical_cores": os.cpu_count() or 1,
            "memory_gb": memory_gb,
        },
        "gpu": {
            "available": has_gpu,
            "devices": gpu_devices,
        },
        "runtimes": {
            "torch": torch_report,
            "paddle": paddle_report,
        },
        "recommendation": {
            "profile": profile,
            "provider_devices": provider_devices,
            "summary": (
                "检测到可用 CUDA 环境，优先使用 GPU 运行高精度本地模型。"
                if has_gpu
                else "未检测到可用 CUDA 环境，将使用 CPU；高精度 OCR 与 LaMa 首次运行会较慢。"
            ),
        },
    }


def torch_cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # A hardware probe must never make the setup endpoint fail.
        return False


def paddle_cuda_available() -> bool:
    try:
        import paddle

        return bool(paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0)
    except Exception:  # Native-loader and partially initialized module errors are possible here.
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
    except Exception:
        pass


def release_paddle_cuda() -> None:
    gc.collect()
    try:
        import paddle

        if paddle.device.is_compiled_with_cuda():
            paddle.device.cuda.empty_cache()
    except Exception:
        pass

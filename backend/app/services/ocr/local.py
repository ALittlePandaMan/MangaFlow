"""Persistent isolated OCR workers; all inference shares the existing model gate."""
from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
from pathlib import Path
from threading import RLock, Thread

from app.core.config import get_settings
from app.services.base import OCRProvider, OCRResult, ProviderCapabilities, ProviderError
from app.services.execution import serialized_model_call
from app.services.infra.device import effective_device_request, is_accelerator_error
from app.services.ocr.local_catalog import (
    LOCAL_OCR_DEFAULT_MAX_TOKENS,
    LOCAL_OCR_MODELS,
    local_ocr_python,
    resolve_assets,
    validate_local_config,
)

PROTOCOL = "mangaflow-local-ocr-v1"


class LocalOCRWorker:
    def __init__(self, provider: str, device: str):
        self.provider, self.device = provider, effective_device_request(device)
        self.effective_device = self.device
        self.fallback_reason = None
        self.process = None
        self.messages = queue.Queue()
        self.lock = RLock()
        self.actual_device = None
        self.sequence = 0
        atexit.register(self.close)

    @staticmethod
    def _read(process, messages):
        for line in process.stdout:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and value.get("protocol") == PROTOCOL:
                messages.put(value)
        messages.put({"error": "OCR worker exited"})

    def _receive(self, timeout):
        try:
            response = self.messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise RuntimeError("OCR 推理超时；工作进程已停止，可重试。") from exc
        if response.get("error"):
            raise RuntimeError(str(response["error"])[:1000])
        return response

    def start(self, directory: Path, auxiliary: Path | None):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return
            self.close()
            python = local_ocr_python()
            if not python.is_file():
                raise RuntimeError("本地 OCR 独立运行环境未安装；请安装 backend/requirements/local-ocr.txt")
            # Pass runtime paths, not API credentials or the parent's config file.
            allowed = {"PATH", "SYSTEMROOT", "WINDIR", "HOME", "TMPDIR", "TEMP", "TMP", "LD_LIBRARY_PATH",
                       "CUDA_VISIBLE_DEVICES", "NVIDIA_VISIBLE_DEVICES", "NVIDIA_DRIVER_CAPABILITIES"}
            env = {key: value for key, value in os.environ.items() if key in allowed}
            env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", TOKENIZERS_PARALLELISM="false",
                       PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1", OMP_NUM_THREADS="2",
                       MANGAFLOW_MODEL_DIR=str(get_settings().model_dir), MANGAFLOW_DATA_DIR=str(get_settings().data_dir))
            command = [str(python), "-u", "-m", "app.services.ocr.local_worker", "--provider", self.provider,
                       "--directory", str(directory), "--device", self.effective_device]
            if auxiliary is not None:
                command += ["--auxiliary", str(auxiliary)]
            self.messages = queue.Queue()
            self.process = subprocess.Popen(command, cwd=Path(__file__).resolve().parents[3], env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            Thread(target=self._read, args=(self.process, self.messages), daemon=True).start()
            try:
                reply = self._receive(240)
                if reply.get("ready") is not True or reply.get("provider") != self.provider:
                    raise RuntimeError("invalid OCR worker handshake")
                self.actual_device = reply["device"]
            except Exception:
                self.close()
                raise

    def recognize(self, source, box, orientation, padding, max_tokens, timeout, *, polygon=None, crop_angle=None):
        with self.lock:
            try:
                if self.process is None or self.process.poll() is not None:
                    raise RuntimeError("OCR worker is not running")
                self.sequence += 1
                request = {"id": self.sequence, "image_path": str(source.resolve()), "bbox": box, "polygon": polygon, "crop_angle": crop_angle,
                           "orientation": orientation, "padding": padding, "max_new_tokens": max_tokens}
                self.process.stdin.write(json.dumps(request, allow_nan=False) + "\n")
                self.process.stdin.flush()
                reply = self._receive(timeout)
                if reply.get("id") != self.sequence or not isinstance(reply.get("text"), str):
                    raise RuntimeError("invalid OCR worker response")
                return reply
            except Exception:
                self.close()
                raise

    def close(self):
        with self.lock:
            process, self.process = self.process, None
            self.actual_device = None
            if process is None:
                return
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()


class IsolatedOCRProvider(OCRProvider):
    def __init__(self, config=None):
        super().__init__(config)
        validate_local_config(self.capabilities.name, self.config)
        spec = LOCAL_OCR_MODELS[self.capabilities.name]
        # Native checkpoint keys must change when the pinned model changes,
        # including configurations created before explicit version fields exist.
        self.config = {"model": spec.repository, "revision": spec.revision, **self.config}
        self.worker = LocalOCRWorker(self.capabilities.name, str(self.config.get("device", "auto")))
        self._device = None

    @serialized_model_call
    def load(self):
        directory, auxiliary = resolve_assets(self.capabilities.name, download=get_settings().auto_provision_models)
        requested = effective_device_request(self.config.get("device", "auto"))
        self._fallback_reason = self.worker.fallback_reason
        try:
            self.worker.start(directory, auxiliary)
        except Exception as exc:
            if requested != "auto" or self.worker.effective_device == "cpu" or not is_accelerator_error(exc):
                raise
            self.worker.close()
            self.worker.effective_device = "cpu"
            self.worker.fallback_reason = "accelerator_initialization_failed"
            self.worker.start(directory, auxiliary)
            self._fallback_reason = "accelerator_initialization_failed"
        self._device = self.worker.actual_device
        if self._device == "cpu" and requested != "cpu" and self._fallback_reason is None:
            self._fallback_reason = "accelerator_unavailable"

    @serialized_model_call
    def unload(self):
        self.worker.close()
        self.worker.effective_device = self.worker.device
        self.worker.fallback_reason = None
        self._device = None
        super().unload()

    @serialized_model_call
    def recognize(self, image_path, bbox, orientation, padding=4, *, polygon=None, crop_angle=None):
        try:
            from app.services.base import ProviderState
            if self.state == ProviderState.READY and (self.worker.process is None or self.worker.process.poll() is not None):
                self.state = ProviderState.UNLOADED
            self.ensure_loaded()
            self._device = self.worker.actual_device
            arguments = (image_path, bbox, orientation, padding,
                         int(self.config.get("max_new_tokens", LOCAL_OCR_DEFAULT_MAX_TOKENS)),
                         int(self.config.get("timeout", 180)))
            geometry = {**({"polygon": polygon} if polygon is not None else {}),
                        **({"crop_angle": crop_angle} if crop_angle is not None else {})}
            try:
                reply = self.worker.recognize(*arguments, **geometry)
            except Exception as exc:
                if (self.config.get("device", "auto") != "auto" or not str(self._device).startswith("cuda")
                        or not is_accelerator_error(exc)):
                    raise
                # Local inference is safe to retry once. Stop the failed GPU
                # process first, so CPU fallback doesn't retain its allocations.
                self.worker.close()
                self.worker.effective_device = "cpu"
                self.worker.fallback_reason = "accelerator_inference_failed"
                directory, auxiliary = resolve_assets(self.capabilities.name)
                self.worker.start(directory, auxiliary)
                self._device = self.worker.actual_device
                self._fallback_reason = "accelerator_inference_failed"
                reply = self.worker.recognize(*arguments, **geometry)
            # Several generation configurations may share one worker. Its
            # device can change after another configuration falls back to CPU.
            self._device = self.worker.actual_device
            self._fallback_reason = self.worker.fallback_reason or (
                "accelerator_unavailable" if self._device == "cpu" and effective_device_request(self.config.get("device", "auto")) != "cpu" else None)
            spec = LOCAL_OCR_MODELS[self.capabilities.name]
            return OCRResult(reply["text"], .8 if reply["text"] else 0., orientation,
                {"model": spec.repository, "revision": spec.revision, "device": self._device,
                 "backend": "torch-worker", "confidence_available": False,
                 "generation_truncated": bool(reply.get("truncated", False))})
        except Exception as exc:
            self.worker.close()
            self.state = ProviderState.ERROR
            raise ProviderError(f"{self.capabilities.name} 识字失败，未覆盖原文：{exc}") from exc


def _capabilities(name, description):
    return ProviderCapabilities(name, "ocr", description=description, devices=["cpu", "cuda"],
        extra={"serialized_model": True, "isolated_runtime": True, "koharu_aligned": True, "backend": "torch-worker"})


class PaddleOCRVLProvider(IsolatedOCRProvider):
    capabilities = _capabilities("paddleocr-vl-1.6", "Koharu 默认 PaddleOCR-VL 1.6，按现有文本框本地识字，不重新检测")


class BaberuOCRProvider(IsolatedOCRProvider):
    capabilities = _capabilities("baberu-ocr", "Koharu 同款 Baberu OCR，支持日文、中文与英文气泡")


class HayaiOCRProvider(IsolatedOCRProvider):
    capabilities = _capabilities("hayai-ocr", "Koharu 同款 Hayai OCR v2，支持日中韩英文本裁剪识字")

"""Persistent isolated RF-DETR worker. No downloads or heavy imports in requests.

RF-DETR 1.7 needs Transformers 5; MangaOCR needs Transformers 4. Running the
classifier in a separate environment avoids breaking the existing OCR model.
"""

from __future__ import annotations

import atexit
import json
import os
import queue
import subprocess
from pathlib import Path
from threading import RLock, Thread
from typing import Any

from app.core.config import get_settings
from app.services.detection.text_roles import classification_options

MODEL_REVISION = "aed55fdb8ca953c6bec33cf6ed6dd52a9b72bfa2"
MODEL_REPOSITORY = "mayocream/koharu-layout-rfdetr-seg-2xl-1152"
MODEL_SHA256 = "9bf6d2cbd7793c956d8c857bb1672a396eb7f100eb0682f86830d05e31168efb"
MODEL_RELATIVE_PATH = "text-classification/koharu-layout-rfdetr-seg-2xl-1152.safetensors"


def classifier_python(options: dict[str, Any]) -> Path:
    configured = options.get("python_executable") or os.environ.get("MANGAFLOW_CLASSIFIER_PYTHON")
    if configured:
        return Path(str(configured))
    bundled = Path("/opt/layout-classifier/bin/python")
    if bundled.is_file():
        return bundled
    runtime = get_settings().model_dir / "classification-runtime"
    return runtime / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


class LayoutTextClassifier:
    def __init__(self, config: dict[str, Any]) -> None:
        self.options = classification_options(config)
        self.device = str(self.options.get("device", config.get("device", "auto")))
        self._process: subprocess.Popen[str] | None = None
        self._messages: queue.Queue[Any] = queue.Queue()
        self._lock = RLock()
        self._unavailable: str | None = None
        atexit.register(self.close)

    def _read_stdout(self, process: subprocess.Popen[str], messages: queue.Queue[Any]) -> None:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict) and value.get("protocol") == "mangaflow-text-classifier-v1":
                messages.put(value)
        messages.put({"error": "worker_exited"})

    def _receive(self, timeout: float) -> dict[str, Any]:
        try:
            response = self._messages.get(timeout=timeout)
        except queue.Empty as exc:
            raise RuntimeError("worker_timeout") from exc
        if response.get("error"):
            raise RuntimeError(str(response["error"]))
        return response

    def _start(self) -> None:
        python = classifier_python(self.options)
        weights = get_settings().model_dir / MODEL_RELATIVE_PATH
        if not python.is_file():
            raise RuntimeError("runtime_not_installed")
        if not weights.is_file():
            raise RuntimeError("model_not_installed")
        environment = {**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"}
        self._messages = queue.Queue()
        self._process = subprocess.Popen(
            [str(python), "-u", "-m", "app.services.detection.classification_worker", "--weights", str(weights), "--device", self.device],
            cwd=Path(__file__).resolve().parents[3], env=environment,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        Thread(target=self._read_stdout, args=(self._process, self._messages), daemon=True).start()
        if self._receive(120).get("ready") is not True:
            raise RuntimeError("invalid_worker_handshake")

    def annotate(self, image_path: Path, regions: list[Any], *, protected_indices: set[int] | None = None) -> list[Any]:
        if not regions:
            return regions
        with self._lock:
            if not self.options.get("enabled", True):
                return self._mark(regions, "disabled", "configuration_disabled")
            if self._unavailable:
                return self._mark(regions, "unavailable", self._unavailable)
            try:
                if self._process is None:
                    self._start()
                assert self._process is not None and self._process.stdin is not None
                request = {
                    "image_path": str(image_path.resolve()), "polygons": [r.polygon for r in regions],
                    "protected_indices": sorted(protected_indices or set()),
                    "filter_sfx": self.options.get("filter_sfx", True),
                    "min_sfx_confidence": float(self.options["min_sfx_confidence"]),
                }
                self._process.stdin.write(json.dumps(request, allow_nan=False) + "\n")
                self._process.stdin.flush()
                response = self._receive(90)
                values = response.get("classifications")
                if not isinstance(values, list) or len(values) != len(regions) or not all(isinstance(v, dict) for v in values):
                    raise RuntimeError("invalid_worker_response")
                for region, value in zip(regions, values, strict=True):
                    region.metadata = {**region.metadata, "text_classification": {**value, "policy": self.policy(), "model": MODEL_REPOSITORY, "model_revision": MODEL_REVISION}}
                return regions
            except Exception as exc:
                # Do not silently remove dialogue on missing models, OOM or
                # transport failures. Circuit-break repeated cold starts.
                self._unavailable = str(exc)[:160]
                self.close()
                return self._mark(regions, "unavailable", self._unavailable)

    def policy(self) -> dict[str, Any]:
        return {
            "enabled": self.options.get("enabled", True) is not False,
            "filter_sfx": self.options.get("filter_sfx", True) is not False,
            "min_sfx_confidence": self.options.get("min_sfx_confidence", 0.6),
        }

    def _mark(self, regions: list[Any], status: str, reason: str) -> list[Any]:
        for region in regions:
            region.metadata = {**region.metadata, "text_classification": {"version": 1, "label": "uncertain", "status": status, "excluded": False, "reason": reason, "policy": self.policy()}}
        return regions

    def close(self) -> None:
        with self._lock:
            process, self._process = self._process, None
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

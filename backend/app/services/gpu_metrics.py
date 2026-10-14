"""Optional GPU telemetry. Never initialize a CUDA context to measure memory."""
from __future__ import annotations

import csv
import io
import shutil
import subprocess
import threading
import time


def parse_gpu_csv(output: str) -> list[dict]:
    devices = []
    for row in csv.reader(io.StringIO(output)):
        if len(row) != 5:
            raise ValueError("Unsupported GPU metrics output")
        index, name, total, used, utilization = (item.strip() for item in row)
        total_bytes, used_bytes = int(float(total) * 1024**2), int(float(used) * 1024**2)
        if not 0 <= used_bytes <= total_bytes or total_bytes <= 0:
            raise ValueError("Invalid GPU memory metrics")
        devices.append({"index": int(index), "name": name, "total_bytes": total_bytes,
            "used_bytes": used_bytes, "free_bytes": total_bytes - used_bytes,
            "utilization_percent": float(utilization) if utilization not in {"N/A", "[N/A]"} else None})
    return devices


class GpuProbe:
    def __init__(self):
        self.lock = threading.Lock()
        self.updated = 0.0
        self.cached: dict | None = None

    def sample(self) -> dict:
        # Called only by the diagnostics API, not page/CPU admission or painting.
        with self.lock:
            if self.cached is not None and time.monotonic() - self.updated < 10:
                return self.cached
            executable = shutil.which("nvidia-smi")
            try:
                if not executable:
                    raise OSError("GPU monitoring unavailable")
                result = subprocess.run([executable, "--query-gpu=index,name,memory.total,memory.used,utilization.gpu",
                    "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=1.5, check=True,
                    creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0)
                devices = parse_gpu_csv(result.stdout)
                self.cached = {"known": bool(devices), "devices": devices}
            except (OSError, ValueError, subprocess.SubprocessError):
                self.cached = {"known": False, "devices": [], "reason": "GPU 指标不可用；本地模型仍保持串行"}
            self.updated = time.monotonic()
            return self.cached

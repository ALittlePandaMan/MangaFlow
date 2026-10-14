"""Bounded in-process execution. No ORM objects or sessions enter CPU workers.

Page orchestration and CPU computation use different pools. Model inference is
serialized until a provider has an independently verified concurrent adapter.
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from functools import lru_cache, wraps
from pathlib import Path
from typing import Literal
from uuid import uuid4

import psutil
from app.core.config import get_settings
from app.services.gpu_metrics import GpuProbe
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)
MiB = 1024**2


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["auto", "fixed", "serial"] = "auto"
    max_pages: int = Field(default=4, ge=1, le=8)
    cpu_workers: int = Field(default=4, ge=1, le=8)
    memory_reserve_mb: int = Field(default=1024, ge=256, le=16384)


class ResourceProbe:
    def __init__(self):
        self.lock = threading.Lock()
        self.updated = 0.0
        self.cached: dict = {}
        self.last_cpu: tuple[float, float] | None = None

    def sample(self) -> dict:
        with self.lock:
            if self.cached and time.monotonic() - self.updated < 2:
                return dict(self.cached)
            try:
                memory = psutil.virtual_memory()
                available, total = memory.available, memory.total
                cores = (
                    len(psutil.Process().cpu_affinity())
                    if hasattr(psutil.Process(), "cpu_affinity")
                    else os.cpu_count() or 1
                )
                # Docker/WSL host totals alone can overestimate a container's budget.
                root = Path("/sys/fs/cgroup")
                if (root / "memory.max").is_file():
                    limit = (root / "memory.max").read_text().strip()
                    if limit.isdigit():
                        total = min(total, int(limit))
                        available = min(available, max(0, int(limit) - int((root / "memory.current").read_text())))
                if (root / "cpu.max").is_file():
                    quota, period = (root / "cpu.max").read_text().split()
                    if quota.isdigit():
                        cores = min(cores, max(1, int(quota) // int(period)))
                cpu_times = psutil.cpu_times()
                total_cpu = sum(cpu_times) - getattr(cpu_times, "guest", 0) - getattr(cpu_times, "guest_nice", 0)
                idle_cpu = cpu_times.idle + getattr(cpu_times, "iowait", 0)
                cpu_percent = 0.0
                if self.last_cpu and total_cpu > self.last_cpu[0]:
                    cpu_percent = 100 * (1 - (idle_cpu - self.last_cpu[1]) / (total_cpu - self.last_cpu[0]))
                self.last_cpu = (total_cpu, idle_cpu)
                self.cached = {
                    "available_bytes": available,
                    "total_bytes": total,
                    "cpu_percent": max(0., min(100., cpu_percent)),
                    "cpu_cores": cores,
                    "rss_bytes": psutil.Process().memory_info().rss,
                    "known": True,
                }
            except (OSError, ValueError, psutil.Error):
                self.cached = {
                    "available_bytes": 0,
                    "total_bytes": 0,
                    "cpu_percent": 0,
                    "cpu_cores": 1,
                    "rss_bytes": 0,
                    "known": False,
                }
            self.updated = time.monotonic()
            return dict(self.cached)


class AdaptiveLimiter:
    """Resize admission, not executors; lowering the limit never kills workers."""

    def __init__(self, name: str, maximum: int, mode: str, reserve: int, probe: ResourceProbe):
        self.name, self.probe = name, probe
        self.lock = threading.RLock()
        self.active = self.reserved = self.peak = 0
        self.samples: dict[str, list[float]] = {}
        self.baselines: dict[str, tuple[int, float]] = {}
        self.cooldown = 0.0
        self.configure(maximum, mode, reserve)

    def configure(self, maximum: int, mode: str, reserve: int):
        with self.lock:
            self.maximum, self.mode, self.reserve = maximum, mode, reserve
            self.limit = 1 if mode == "serial" else maximum if mode == "fixed" else min(2, maximum)
            self.samples.clear()
            self.baselines.clear()
            self.reason = "串行模式" if mode == "serial" else "固定上限" if mode == "fixed" else "保守起步"

    def try_acquire(self, estimate: int = 0) -> bool:
        with self.lock:
            stats = self.probe.sample()
            cap = self.limit
            if self.mode == "auto" and self.name == "cpu":
                cap = min(cap, max(1, stats.get("cpu_cores", 1) // get_settings().native_threads))
            if not stats["known"]:
                cap, self.reason = 1, "资源指标不可用，保守串行"
            elif stats["available_bytes"] < self.reserve + self.reserved + estimate:
                cap, self.reason = 1, "内存余量不足，降低并发"
            elif self.mode == "auto" and self.name == "cpu" and stats["cpu_percent"] > 90:
                cap, self.reason = max(1, cap // 2), "CPU 繁忙，降低并发"
            if cap < self.limit and self.mode == "auto":
                self.limit = cap
                self.cooldown = time.monotonic() + 15
                self.samples.clear()
                self.baselines.clear()
            if self.active >= cap:
                return False
            self.active += 1
            self.reserved += estimate
            self.peak = max(self.peak, self.active)
            return True

    def release(
        self, elapsed: float, estimate: int = 0, *, kind: str = "default", units: float = 1, failed: bool = False
    ):
        with self.lock:
            self.active -= 1
            self.reserved -= estimate
            if failed:
                if self.mode == "auto":
                    self.limit = max(1, self.limit // 2)
                    self.cooldown = time.monotonic() + 30
                    self.samples.clear()
                    self.baselines.clear()
                    self.reason = "任务异常，暂时降低并发"
                return
            if self.mode != "auto" or time.monotonic() < self.cooldown:
                return
            values = self.samples.setdefault(kind, [])
            values.append(max(0.001, elapsed) / max(0.1, units))
            if len(values) < max(4, self.limit * 2):
                return
            # Compare like stages, normalized by workload; never compare OCR
            # latency to translation latency. This is a probe, not a speed promise.
            throughput = self.limit / (sum(values) / len(values))
            values.clear()
            baseline = self.baselines.get(kind)
            if baseline and self.limit > baseline[0] and throughput < baseline[1] * 1.05:
                self.limit = baseline[0]
                self.cooldown = time.monotonic() + 30
                self.baselines.clear()
                self.reason = "增加并发无明显收益，回退"
            elif self.limit < self.maximum:
                self.baselines[kind] = (self.limit, throughput)
                self.limit += 1
                self.reason = "小幅增加并发，观察吞吐量"

    def snapshot(self):
        with self.lock:
            return {
                "active": self.active,
                "limit": self.limit,
                "maximum": self.maximum,
                "peak": self.peak,
                "reason": self.reason,
            }


class ExecutionRuntime:
    def __init__(self):
        settings = get_settings()
        self.policy_path = settings.data_dir / "execution-policy.json"
        self.policy = ExecutionPolicy(
            mode=settings.execution_mode,
            max_pages=settings.task_concurrency,
            cpu_workers=settings.cpu_workers,
            memory_reserve_mb=settings.memory_reserve_mb,
        )
        if self.policy_path.is_file():
            try:
                self.policy = ExecutionPolicy.model_validate_json(self.policy_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                logger.exception("Ignoring invalid execution policy; using configured defaults")
        self.probe = ResourceProbe()
        self.gpu_probe = GpuProbe()
        self.policy_lock = threading.RLock()
        self.pages = AdaptiveLimiter(
            "pages", self.policy.max_pages, self.policy.mode, self.policy.memory_reserve_mb * MiB, self.probe
        )
        self.cpu = AdaptiveLimiter(
            "cpu", self.policy.cpu_workers, self.policy.mode, self.policy.memory_reserve_mb * MiB, self.probe
        )
        self.cpu_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mangaflow-cpu")
        self.model_lock = threading.RLock()
        self.model_active = 0
        self.model_depth = threading.local()
        self.model_metrics_lock = threading.Lock()
        self.model_calls = 0
        self.model_wait_seconds = 0.0
        self.model_run_seconds = 0.0
        self.recent = deque(maxlen=100)
        self.native_threads = settings.native_threads

    def update(self, policy: ExecutionPolicy, *, persist: bool = True):
        with self.policy_lock:
            if persist:
                temporary = self.policy_path.with_name(f".{uuid4().hex}.json")
                try:
                    temporary.write_text(policy.model_dump_json(indent=2), encoding="utf-8")
                    temporary.replace(self.policy_path)
                finally:
                    temporary.unlink(missing_ok=True)
            self.policy = policy
            self.pages.configure(policy.max_pages, policy.mode, policy.memory_reserve_mb * MiB)
            self.cpu.configure(policy.cpu_workers, policy.mode, policy.memory_reserve_mb * MiB)

    @contextmanager
    def model_slot(self):
        # Reentrant: Hybrid -> LaMa and recognize -> ensure_loaded reuse a slot.
        waiting = time.monotonic()
        with self.model_lock:
            depth = getattr(self.model_depth, "depth", 0)
            started = time.monotonic()
            self.model_depth.depth = depth + 1
            self.model_active = 1
            try:
                yield
            finally:
                self.model_depth.depth = depth
                if not depth:
                    self.model_active = 0
                    with self.model_metrics_lock:
                        self.model_calls += 1
                        self.model_wait_seconds += started - waiting
                        self.model_run_seconds += time.monotonic() - started

    def map_cpu(self, jobs, *, check: Callable[[], None], estimate: int, kind: str = "mask", units: float = 1):
        """Lazy bounded submission; caller alone checks cancellation / writes DB.

        Drain on error/cancel so workers cannot write files after history closes.
        Jobs must be pure computation callables and never wait on this same pool.
        """
        iterator = iter(jobs)
        pending: dict[Future, int] = {}
        next_job = next(iterator, None)
        index = 0

        def perform(job):
            started = time.monotonic()
            failed = True
            try:
                result = job()
                failed = False
                return result
            finally:
                self.cpu.release(time.monotonic() - started, estimate, kind=kind, units=units, failed=failed)

        try:
            while next_job is not None or pending:
                check()
                if next_job is not None and self.cpu.try_acquire(estimate):
                    try:
                        future = self.cpu_pool.submit(perform, next_job)
                    except BaseException:
                        self.cpu.release(0, estimate, failed=True)
                        raise
                    pending[future] = index
                    index += 1
                    next_job = next(iterator, None)
                    continue
                if pending:
                    done, _ = wait(pending, timeout=0.05, return_when=FIRST_COMPLETED)
                    for future in done:
                        order = pending.pop(future)
                        result = future.result()
                        check()
                        yield order, result
                else:
                    time.sleep(0.05)
        finally:
            # Submission never exceeds admitted workers; do not cancel futures
            # without releasing their reservation. Draining is bounded by work.
            wait(pending)

    def record_stage(self, task_id: str, stage: str, seconds: float):
        with self.policy_lock:
            self.recent.append({"task_id": task_id, "stage": stage, "seconds": round(seconds, 3)})

    def snapshot(self):
        # Optional driver probing must not hold the policy lock or block inference.
        gpu = self.gpu_probe.sample()
        with self.model_metrics_lock:
            model_metrics = {"calls": self.model_calls, "wait_seconds": round(self.model_wait_seconds, 3), "run_seconds": round(self.model_run_seconds, 3)}
        with self.policy_lock:
            timings: dict[str, list[float]] = {}
            for sample in self.recent:
                timings.setdefault(sample["stage"], []).append(sample["seconds"])
            stage_timings = [{"stage": stage, "samples": len(values), "mean_seconds": round(sum(values) / len(values), 3),
                "p95_seconds": sorted(values)[min(len(values) - 1, int(len(values) * .95))]} for stage, values in sorted(timings.items())]
            return {
                "policy": self.policy.model_dump(),
                "pages": self.pages.snapshot(),
                "cpu": self.cpu.snapshot(),
                "models": {"active": self.model_active, "limit": 1, **model_metrics},
                "gpu": gpu,
                "stage_timings": stage_timings,
                "resources": self.probe.sample(),
                "native_threads": self.native_threads,
                "recent_stages": list(self.recent),
            }


@lru_cache(maxsize=1)
def execution_runtime() -> ExecutionRuntime:
    return ExecutionRuntime()


def configure_native_threads():
    """Process-wide knobs: configure once at startup, never during inference."""
    threads = get_settings().native_threads
    os.environ.setdefault("OMP_NUM_THREADS", str(threads))
    os.environ.setdefault("MKL_NUM_THREADS", str(threads))
    import cv2

    cv2.setNumThreads(threads)
    if "torch" in sys.modules:
        sys.modules["torch"].set_num_threads(threads)


def serialized_model_call(method):
    @wraps(method)
    def call(self, *args, **kwargs):
        with execution_runtime().model_slot(), self._load_lock:
            return method(self, *args, **kwargs)

    return call

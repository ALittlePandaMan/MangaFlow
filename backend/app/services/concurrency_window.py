"""Windowed concurrency probes based on completed work and tail latency.

Resource owners provide locking and admission. No extra benchmark work is sent.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field


@dataclass
class Window:
    started: float
    latencies: list[float] = field(default_factory=list)
    units: float = 0


class ConcurrencyWindow:
    minimum_seconds = 30
    minimum_samples = 8
    cooldown_seconds = 60

    def __init__(self):
        self.reset()

    def reset(self, *, cooldown: float = 0):
        self.epoch = time.monotonic()
        self.after = self.epoch + cooldown
        self.windows: dict[str, Window] = {}
        self.baseline: tuple[str, int, float, float] | None = None
        self.demand = False

    def blocked(self):
        self.demand = True

    def observe(self, elapsed: float, units: float, kind: str, limit: int, maximum: int) -> tuple[int, str | None]:
        now = time.monotonic()
        if now < self.after or now - elapsed < self.epoch:
            return limit, None
        units = max(.1, units)
        window = self.windows.setdefault(kind, Window(now - elapsed))
        window.latencies.append(max(.001,elapsed) / units)
        window.units += units
        duration = now - window.started
        if duration < self.minimum_seconds or len(window.latencies) < self.minimum_samples:
            return limit, None
        throughput = window.units / max(.001,duration)
        p95 = sorted(window.latencies)[math.ceil(len(window.latencies)*.95)-1]
        self.windows.pop(kind)
        baseline = self.baseline
        if baseline and kind != baseline[0]:
            return limit, None  # Complete this resource's original probe first.
        if baseline:
            _, old_limit, old_throughput, old_p95 = baseline
            self.reset(cooldown=self.cooldown_seconds)
            if throughput < old_throughput * 1.1 or p95 > old_p95 * 1.2:
                return old_limit, "吞吐提升不足 10% 或 P95 增长超过 20%，回退并冷却"
            return limit, "并发收益已验证，冷却后继续观察"
        if limit < maximum and self.demand:
            self.reset()
            self.baseline = (kind,limit,throughput,p95)
            return limit+1, "观察满 30 秒且至少 8 个样本，试增一路并发"
        return limit, None

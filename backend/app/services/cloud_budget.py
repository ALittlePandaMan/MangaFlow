"""Process-wide, credential-isolated request budgets; no quota probing requests.

OCR and translation using the same origin/key/model share pacing and cooldown.
This is not a provider quota discovery API: RPM/TPM quotas can change independently.
"""
from __future__ import annotations

import hashlib
import math
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import RLock
from typing import Any
from urllib.parse import urlsplit

from app.services.base import ProviderError


def retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, seconds) if math.isfinite(seconds) else None


def validate_concurrency(config: dict[str, Any]) -> None:
    mode = config.get("concurrency_mode", "adaptive")
    if not isinstance(mode, str) or mode not in {"serial", "fixed", "adaptive"}:
        raise ProviderError("并发模式必须为 serial、fixed 或 adaptive。")
    for key, default, maximum in (("max_concurrency", 8, 16), ("requests_per_minute", 60, 6000)):
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or int(value) != value or not 1 <= value <= maximum:
            raise ProviderError(f"{key} 必须是 1–{maximum} 的整数。")


class RequestBudget:
    def __init__(self) -> None:
        self.lock = RLock()
        self.policy = ("adaptive", 8, 60)
        self.limit = 2
        self.active = 0
        self.next_start = 0.0
        self.cooldown_until = 0.0
        self.last_used = time.monotonic()
        self.latencies: list[float] = []
        self.baseline: tuple[int, float] | None = None
        self.probe_after = 0.0
        self.failures = 0

    def configure(self, config: dict[str, Any]) -> None:
        validate_concurrency(config)
        policy = (str(config.get("concurrency_mode", "adaptive")), int(config.get("max_concurrency", 8)), int(config.get("requests_per_minute", 60)))
        with self.lock:
            if policy != self.policy or (not self.active and time.monotonic() - self.last_used > 600):
                self.policy = policy
                self.limit = 1 if policy[0] == "serial" else min(2, policy[1]) if policy[0] == "adaptive" else policy[1]
                self.latencies.clear()
                self.baseline = None
                self.failures = 0
                # Never clear an upstream cooldown when changing settings.

    def try_acquire(self) -> bool:
        with self.lock:
            now = time.monotonic()
            if self.active >= self.limit or now < max(self.next_start, self.cooldown_until):
                return False
            self.active += 1
            self.last_used = now
            # Pace EVERY attempt, including retries. No burst after a long idle.
            self.next_start = now + 60 / self.policy[2]
            return True

    def finish(self, elapsed: float, error: ProviderError | None = None, *, learn: bool = True) -> None:
        with self.lock:
            self.active -= 1
            self.last_used = now = time.monotonic()
            overloaded = error and (error.status_code in {408, 429} or (error.status_code or 0) >= 500 or error.status_code == 0)
            if overloaded:
                self.failures += 1
                # One congestion event per cooldown, not one halving per in-flight response.
                if now >= self.cooldown_until and self.policy[0] == "adaptive":
                    self.limit = max(1, self.limit // 2)
                delay = error.retry_after if error.retry_after is not None else min(2 ** min(self.failures, 5), 30)
                self.cooldown_until = max(self.cooldown_until, now + max(1, delay))
                self.probe_after = self.cooldown_until + 30
                self.latencies.clear()
                self.baseline = None
                return
            if error or not learn or self.policy[0] != "adaptive" or now < self.probe_after:
                return
            self.failures = 0
            self.latencies.append(max(.001, elapsed))
            if len(self.latencies) < max(4, self.limit * 2):
                return
            latency = sum(self.latencies) / len(self.latencies)
            throughput = self.limit / latency
            self.latencies.clear()
            if self.baseline and self.limit > self.baseline[0] and throughput < self.baseline[1] * 1.05:
                # Increasing concurrency without a useful gain adds load, so back off.
                self.limit = self.baseline[0]
                self.baseline = None
                self.probe_after = now + 60
            elif self.limit < self.policy[1] and throughput < self.policy[2] / 60:
                self.baseline = (self.limit, throughput)
                self.limit += 1

    def status(self) -> str:
        with self.lock:
            label = {"serial": "串行", "fixed": "固定并发", "adaptive": "自适应并发"}[self.policy[0]]
            remaining = max(0, math.ceil(self.cooldown_until - time.monotonic()))
            return f"{label} {self.active}/{self.limit}" + (f" · 限流退避 {remaining}s" if remaining else "")

    def poll_interval(self) -> float:
        with self.lock:
            if self.active >= self.limit:
                return .1
            delay = max(self.next_start, self.cooldown_until) - time.monotonic()
            return min(.1, max(.005, delay))


_budgets: dict[tuple[str, str, str], RequestBudget] = {}
_lock = RLock()


def request_budget(config: dict[str, Any]) -> RequestBudget:
    url = urlsplit(str(config.get("base_url", "")))
    origin = f"{url.scheme.lower()}://{(url.hostname or '').lower()}:{url.port or (443 if url.scheme == 'https' else 80)}"
    fingerprint = hashlib.sha256(str(config.get("api_key") or "").strip().encode()).hexdigest()
    key = (origin, fingerprint, str(config.get("model") or "").strip())
    with _lock:
        # Keep active/cooling budgets even when expiring old idle connections.
        for old_key, budget in list(_budgets.items()):
            if not budget.active and time.monotonic() - max(budget.last_used, budget.cooldown_until) > 3600:
                del _budgets[old_key]
        return _budgets.setdefault(key, RequestBudget())

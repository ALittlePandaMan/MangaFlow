"""Task-local timing without ORM objects, payloads, or credentials in workers."""
from __future__ import annotations

import inspect
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps


class TimingCollector:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.values: dict[str, dict[str, float | int]] = {}

    def add(self, name: str, seconds: float) -> None:
        with self.lock:
            value = self.values.setdefault(name, {"seconds": 0.0, "calls": 0})
            value["seconds"] += max(0.0, seconds)
            value["calls"] += 1

    def snapshot(self) -> dict:
        with self.lock:
            return {name: {"seconds": round(value["seconds"], 6), "calls": value["calls"]}
                    for name, value in self.values.items()}


current_timings: ContextVar[TimingCollector | None] = ContextVar("task_timings", default=None)


def record_timing(name: str, seconds: float) -> None:
    collector = current_timings.get()
    if collector is not None:
        collector.add(name, seconds)


@contextmanager
def measure(name: str) -> Iterator[None]:
    started = time.monotonic()
    try:
        yield
    finally:
        record_timing(name, time.monotonic() - started)


def measured(name: str):
    def decorate(function):
        if inspect.iscoroutinefunction(function):
            @wraps(function)
            async def asynchronous(*args, **kwargs):
                with measure(name):
                    return await function(*args, **kwargs)
            return asynchronous

        @wraps(function)
        def synchronous(*args, **kwargs):
            with measure(name):
                return function(*args, **kwargs)
        return synchronous
    return decorate

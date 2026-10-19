"""Persistent attempt timings. Diagnostics must never fail a processing task.

Stage/component durations are inclusive (OCR may run inside translation, CPU
jobs may overlap). They must NOT be summed to derive task or batch wall time.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from uuid import uuid4

from app.core.database import SessionLocal
from app.core.timing import TimingCollector, current_timings
from app.core.transactions import retry_write
from app.models import ProcessingTask, TaskExecutionRun

logger = logging.getLogger(__name__)
_run: ContextVar[tuple[str, float] | None] = ContextVar("execution_run", default=None)


def flush_task_timings(*, terminal: bool = False) -> None:
    run, collector = _run.get(), current_timings.get()
    if run is None or collector is None:
        return
    run_id, started = run
    # Freeze values before opening a write transaction; never collect text,
    # provider credentials or request bodies in this diagnostic table.
    timings = collector.snapshot()
    elapsed = time.monotonic() - started
    try:
        with SessionLocal() as db:
            def save():
                row = db.get(TaskExecutionRun, run_id)
                if row is None:
                    return
                row.timings = timings
                row.wall_seconds = elapsed
                if terminal:
                    task = db.get(ProcessingTask, row.task_id)
                    row.status = task.status if task else "INTERRUPTED"
                    if row.status == "QUEUED" and task and task.waiting_reason in {"previous_page_translation","previous_page_paused"}:
                        row.status = "WAITING_DEPENDENCY"
                    if row.status in {"QUEUED", "RUNNING", "CANCELLING"}:
                        row.status = "INTERRUPTED"
                    row.finished_at = datetime.now(timezone.utc)
            # Never add multi-second lock retries just to save diagnostics.
            retry_write(db, save, attempts=1, lock_timeout_ms=100)
    except Exception:
        logger.warning("Unable to persist task timing sample %s", run_id, exc_info=True)


@contextmanager
def record_task_execution(task_id: str):
    collector = TimingCollector()
    timing_token = current_timings.set(collector)
    run_token = _run.set(None)
    started = time.monotonic()
    run_id = str(uuid4())
    try:
        try:
            with SessionLocal() as db:
                def create():
                    task = db.get(ProcessingTask, task_id)
                    if task is None or task.status != "QUEUED" or task.pause_requested:
                        return False
                    now = datetime.now(timezone.utc)
                    queued = task.updated_at
                    if queued.tzinfo is None:
                        queued = queued.replace(tzinfo=timezone.utc)
                    db.add(TaskExecutionRun(id=run_id, task_id=task_id, started_at=now,
                        queue_seconds=max(0, (now - queued).total_seconds())))
                    return True
                if retry_write(db, create, attempts=1, lock_timeout_ms=100):
                    _run.set((run_id, started))
        except Exception:
            logger.warning("Unable to initialize task timing sample %s", run_id, exc_info=True)
        yield
    finally:
        try:
            flush_task_timings(terminal=True)
        finally:
            _run.reset(run_token)
            current_timings.reset(timing_token)

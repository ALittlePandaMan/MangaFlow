from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import ProcessingTask
from app.models.enums import TaskStatus
from app.pipeline.processor import PipelineProcessor, TaskCancelled, TaskPaused

logger = logging.getLogger(__name__)


@dataclass
class _TaskAttempt:
    """In-memory identity for one dispatch of a persisted task row."""

    generation: int
    cancelled: bool = False
    started: bool = False
    wrapper: asyncio.Task[None] | None = None


class TaskManager:
    """In-process development queue with an API compatible path to external workers."""

    def __init__(self) -> None:
        self.running: dict[str, asyncio.Task[None]] = {}
        self.semaphore = asyncio.Semaphore(get_settings().task_concurrency)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pending: set[str] = set()
        self.workers_started: set[str] = set()
        self.attempts: dict[str, _TaskAttempt] = {}
        self.lock = threading.Lock()

    def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        # Test clients and development reloads may create a fresh event loop.
        # A semaphore that waited on the previous loop cannot be reused.
        self.semaphore = asyncio.Semaphore(get_settings().task_concurrency)

    def dispatch(self, task_id: str) -> None:
        if self.loop is None:
            raise RuntimeError("Task manager has not been started by the application lifespan")
        with self.lock:
            current = self.attempts.get(task_id)
            if current and not current.cancelled and (
                task_id in self.pending or current.started or bool(current.wrapper and not current.wrapper.done())
            ):
                return
            attempt = _TaskAttempt(generation=(current.generation + 1) if current else 1)
            self.attempts[task_id] = attempt
            self.pending.add(task_id)
        self.loop.call_soon_threadsafe(self._schedule, task_id, attempt)

    def _schedule(self, task_id: str, attempt: _TaskAttempt) -> None:
        with self.lock:
            if self.attempts.get(task_id) is not attempt:
                return
            self.pending.discard(task_id)
            if attempt.cancelled:
                self.attempts.pop(task_id, None)
                return
        wrapper = asyncio.create_task(
            self._run(task_id, attempt),
            name=f"mangaflow-{task_id}-{attempt.generation}",
        )
        wrapper.add_done_callback(lambda finished: self._task_finished(task_id, attempt, finished))
        with self.lock:
            if self.attempts.get(task_id) is not attempt or attempt.cancelled:
                wrapper.cancel()
                return
            attempt.wrapper = wrapper
            self.running[task_id] = wrapper

    async def _run(self, task_id: str, attempt: _TaskAttempt) -> None:
        async with self.semaphore:
            with self.lock:
                # A cancelled queued attempt may still reach this point after
                # the same persisted task has already been retried. Only the
                # newest dispatch is allowed to observe that task row.
                if self.attempts.get(task_id) is not attempt or attempt.cancelled:
                    return
                attempt.started = True
                self.workers_started.add(task_id)
            # Model initialization and inference are CPU-bound synchronous work.
            # Running the pipeline coroutine directly on Uvicorn's event loop
            # prevents even the initial 202 response and task polling from being
            # served until inference finishes, which surfaces as an Nginx 504.
            try:
                await asyncio.to_thread(self._run_blocking, task_id)
            finally:
                with self.lock:
                    attempt.started = False
                    reconcile_cancellation = attempt.cancelled
                    if self.attempts.get(task_id) is attempt:
                        self.workers_started.discard(task_id)
                if reconcile_cancellation:
                    # If cancellation landed while _run_blocking was unwinding,
                    # its last status check may already have passed. Reconcile
                    # after atomically publishing that this worker has stopped.
                    await asyncio.to_thread(self._reconcile_cancellation, task_id)

    def _task_finished(
        self,
        task_id: str,
        attempt: _TaskAttempt,
        wrapper: asyncio.Task[None],
    ) -> None:
        with self.lock:
            attempt.started = False
            if self.running.get(task_id) is wrapper:
                self.running.pop(task_id, None)
            if self.attempts.get(task_id) is attempt:
                self.pending.discard(task_id)
                self.workers_started.discard(task_id)
                self.attempts.pop(task_id, None)

    @staticmethod
    def _run_blocking(task_id: str) -> None:
        db = SessionLocal()
        processor: PipelineProcessor | None = None
        try:
            task = db.get(ProcessingTask, task_id)
            if task is None:
                return
            if task.status in {TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}:
                TaskManager._finish_cancelled_task(task)
                db.commit()
                return
            task.status = TaskStatus.RUNNING.value
            task.pause_requested = False
            task.started_at = datetime.now(timezone.utc)
            task.finished_at = None
            task.error_message = None
            db.commit()
            # Translation stages are asynchronous, so give the worker thread a
            # private event loop while keeping the web server loop responsive.
            processor = PipelineProcessor(db)
            asyncio.run(processor.execute(task_id))
            db.refresh(task)
            if task.status in {TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}:
                TaskManager._finish_cancelled_task(task)
                db.commit()
            elif task.status != TaskStatus.PAUSED.value:
                task.status = TaskStatus.COMPLETED.value
                task.progress = 1.0
                task.current_stage = None
                task.message = "Completed"
                task.finished_at = datetime.now(timezone.utc)
                db.commit()
        except TaskPaused:
            # The processor persists both the paused task and restored page
            # state at the interruption boundary.
            db.rollback()
            task = db.get(ProcessingTask, task_id)
            if task and task.status in {TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}:
                TaskManager._finish_cancelled_task(task)
                db.commit()
        except TaskCancelled:
            db.rollback()
            task = db.get(ProcessingTask, task_id)
            if task is not None:
                if processor is not None:
                    processor.finish_interrupted_page()
                TaskManager._finish_cancelled_task(task)
                db.commit()
        except Exception as exc:
            db.rollback()
            task = db.get(ProcessingTask, task_id)
            if task and task.status in {TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}:
                if processor is not None:
                    processor.finish_interrupted_page()
                TaskManager._finish_cancelled_task(task)
                db.commit()
            elif task:
                logger.exception("Processing task %s failed", task_id)
                task.status = TaskStatus.FAILED.value
                task.error_message = str(exc)
                task.message = "Failed"
                task.current_stage = None
                task.finished_at = datetime.now(timezone.utc)
                if task.page:
                    task.page.status = "FAILED"
                    task.page.error_message = str(exc)
                    task.page.current_stage = None
                db.commit()
        finally:
            db.close()

    @staticmethod
    def _finish_cancelled_task(task: ProcessingTask) -> None:
        task.status = TaskStatus.CANCELLED.value
        task.current_stage = None
        task.pause_requested = False
        task.error_message = None
        task.message = "Cancelled"
        task.finished_at = datetime.now(timezone.utc)

    @staticmethod
    def _reconcile_cancellation(task_id: str) -> None:
        with SessionLocal() as db:
            task = db.get(ProcessingTask, task_id)
            if task and task.status == TaskStatus.CANCELLING.value:
                TaskManager._finish_cancelled_task(task)
                db.commit()

    def cancel(self, task_id: str) -> bool:
        # asyncio cannot stop code already running inside to_thread(). Cancelling
        # only the wrapper would release the semaphore while inference keeps
        # running, allowing a second GPU job to overlap it. Started work observes
        # CANCELLING cooperatively; queued work is invalidated by attempt identity.
        with self.lock:
            attempt = self.attempts.get(task_id)
            if attempt is None:
                return False
            attempt.cancelled = True
            self.pending.discard(task_id)
            running = attempt.wrapper
            worker_started = attempt.started
        if running and not running.done() and not worker_started and self.loop:
            self.loop.call_soon_threadsafe(running.cancel)
        return worker_started

    def has_started(self, task_id: str) -> bool:
        with self.lock:
            attempt = self.attempts.get(task_id)
            return bool(attempt and attempt.started)

    def is_active(self, task_id: str) -> bool:
        with self.lock:
            attempt = self.attempts.get(task_id)
            if attempt is None:
                return False
            if attempt.started:
                return True
            return not attempt.cancelled and (
                task_id in self.pending or bool(attempt.wrapper and not attempt.wrapper.done())
            )

    def recover_interrupted(self) -> None:
        with SessionLocal() as db:
            interrupted = list(
                db.scalars(
                    select(ProcessingTask).where(
                        ProcessingTask.status.in_([TaskStatus.RUNNING.value, TaskStatus.CANCELLING.value])
                    )
                ).all()
            )
            for task in interrupted:
                if task.status == TaskStatus.CANCELLING.value:
                    interrupted_stage = task.current_stage
                    self._finish_cancelled_task(task)
                    if task.page:
                        task.page.current_stage = None
                        task.page.error_message = None
                        task.page.status = PipelineProcessor.stable_status_before(interrupted_stage, task.page.status)
                else:
                    task.status = TaskStatus.FAILED.value
                    task.error_message = "Application stopped while this in-process task was running; retry is available"
                    task.message = "Interrupted"
                    task.current_stage = None
                    task.finished_at = datetime.now(timezone.utc)
                    if task.page:
                        task.page.status = "FAILED"
                        task.page.error_message = task.error_message
                        task.page.current_stage = None

            # A previous process may have marked the task as interrupted before
            # this page-state repair was introduced. Reconcile those pages too,
            # so a browser refresh cannot leave the editor stuck on "OCR running".
            stale_interrupted = list(
                db.scalars(
                    select(ProcessingTask).where(
                        ProcessingTask.status == TaskStatus.FAILED.value,
                        ProcessingTask.message == "Interrupted",
                    )
                ).all()
            )
            for task in stale_interrupted:
                if task.page and task.page.current_stage:
                    task.page.status = "FAILED"
                    task.page.error_message = task.error_message
                    task.page.current_stage = None
            queued = list(
                db.scalars(select(ProcessingTask).where(ProcessingTask.status == TaskStatus.QUEUED.value)).all()
            )
            db.commit()
        for task in queued:
            self.dispatch(task.id)


task_manager = TaskManager()

from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.transactions import is_sqlite_busy, retry_write
from app.models import ProcessingTask
from app.models.enums import TaskStatus
from app.pipeline.processor import PipelineProcessor, TaskCancelled, TaskPaused
from app.services.execution import execution_runtime

logger = logging.getLogger(__name__)


@dataclass
class _TaskAttempt:
    """In-memory identity for one dispatch of a persisted task row."""

    generation: int
    cancelled: bool = False
    started: bool = False
    wrapper: asyncio.Task[None] | None = None
    page_id: str = ""
    estimate: int = 0
    kind: str = "full"
    units: float = 1.0


class TaskManager:
    """In-process development queue with an API compatible path to external workers."""

    def __init__(self) -> None:
        self.running: dict[str, asyncio.Task[None]] = {}
        self.executor: ThreadPoolExecutor | None = None
        self.page_locks: dict[str, asyncio.Lock] = {}
        self.stopping = False
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pending: set[str] = set()
        self.workers_started: set[str] = set()
        self.attempts: dict[str, _TaskAttempt] = {}
        self.lock = threading.Lock()
        self.terminal_errors: dict[str, str] = {}

    def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        # Test clients and reloads may create a fresh event loop.
        self.page_locks = {}
        self.stopping = False
        self.executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="mangaflow-page")

    async def shutdown(self) -> None:
        # Never release page/resource ownership while its thread still writes.
        # Queued DB rows remain queued for recovery on the next startup.
        self.stopping = True
        for _identifier, attempt in list(self.attempts.items()):
            if not attempt.started and attempt.wrapper:
                attempt.wrapper.cancel()
        if self.running:
            await asyncio.gather(*list(self.running.values()), return_exceptions=True)
        if self.executor:
            self.executor.shutdown(wait=True)
            self.executor = None

    def dispatch(self, task_id: str) -> None:
        if self.loop is None:
            raise RuntimeError("Task manager has not been started by the application lifespan")
        if self.stopping:
            return
        with SessionLocal() as db:
            task = db.get(ProcessingTask, task_id)
            if task is None:
                return
            page_id = task.image_id
            pixels = task.page.width * task.page.height if task.page else 1_000_000
            kind = str((task.payload or {}).get("start_stage") or task.task_type)
        with self.lock:
            current = self.attempts.get(task_id)
            if current and not current.cancelled and (
                task_id in self.pending or current.started or bool(current.wrapper and not current.wrapper.done())
            ):
                return
            attempt = _TaskAttempt(generation=(current.generation + 1) if current else 1)
            attempt.page_id = page_id
            attempt.estimate = 128 * 1024**2 + pixels * 48
            attempt.kind = kind
            attempt.units = max(.25, pixels / 1_000_000)
            self.terminal_errors.pop(task_id, None)
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
        # Wait for this page BEFORE taking global capacity. A long same-page
        # queue must not starve independent pages. Locks live on this loop only.
        page_lock = self.page_locks.setdefault(attempt.page_id, asyncio.Lock())
        async with page_lock:
            runtime = execution_runtime()
            while not runtime.pages.try_acquire(attempt.estimate):
                if self.stopping or attempt.cancelled:
                    return
                await asyncio.sleep(.1)
            started = time.monotonic()
            failed = True
            with self.lock:
                # A cancelled queued attempt may still reach this point after
                # the same persisted task has already been retried. Only the
                # newest dispatch is allowed to observe that task row.
                if self.attempts.get(task_id) is not attempt or attempt.cancelled:
                    runtime.pages.release(0, attempt.estimate)
                    return
                attempt.started = True
                self.workers_started.add(task_id)
            # Model initialization and inference are CPU-bound synchronous work.
            # Running the pipeline coroutine directly on Uvicorn's event loop
            # prevents even the initial 202 response and task polling from being
            # served until inference finishes, which surfaces as an Nginx 504.
            try:
                future = self.loop.run_in_executor(self.executor, self._run_blocking, task_id)
                try:
                    outcome = await asyncio.shield(future)
                    failed = outcome == TaskStatus.FAILED.value
                except asyncio.CancelledError:
                    # asyncio cancellation does not kill a running native call.
                    await asyncio.shield(future)
                    raise
            except Exception as exc:
                # The failure handler can itself fail (e.g. SQLite is locked).
                # Keep ownership of this page until the terminal state is saved;
                # expose the failure immediately instead of polling stale RUNNING.
                error = str(exc)
                with self.lock:
                    self.terminal_errors[task_id] = error
                logger.exception("Task %s exited without a persisted terminal state", task_id)
                while True:
                    try:
                        await asyncio.to_thread(self._recover_failed_task, task_id, error)
                        with self.lock:
                            self.terminal_errors.pop(task_id, None)
                        break
                    except Exception as recovery_error:
                        logger.warning("Terminal recovery for task %s failed: %s", task_id, type(recovery_error).__name__)
                        if self.stopping or not is_sqlite_busy(recovery_error):
                            break
                        await asyncio.sleep(1)
            finally:
                runtime.pages.release(time.monotonic() - started, attempt.estimate, kind=attempt.kind, units=attempt.units, failed=failed)
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
        if not wrapper.cancelled() and (error := wrapper.exception()) is not None:
            logger.error("Task wrapper %s failed", task_id, exc_info=(type(error), error, error.__traceback__))
        with self.lock:
            attempt.started = False
            if self.running.get(task_id) is wrapper:
                self.running.pop(task_id, None)
            if self.attempts.get(task_id) is attempt:
                self.pending.discard(task_id)
                self.workers_started.discard(task_id)
                self.attempts.pop(task_id, None)
            if not any(item.page_id == attempt.page_id for item in self.attempts.values()):
                page_lock = self.page_locks.get(attempt.page_id)
                if page_lock and not page_lock.locked():
                    self.page_locks.pop(attempt.page_id, None)

    @staticmethod
    def _run_blocking(task_id: str) -> str | None:
        from app.services.editor_history import begin_task_history, finish_task_history
        db = SessionLocal()
        processor: PipelineProcessor | None = None
        history_entry_id: int | None = None
        terminal_persisted = True
        task = None
        try:
            task = db.get(ProcessingTask, task_id)
            if task is None:
                return
            if task.status in {TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}:
                TaskManager._finish_cancelled_task(task)
                db.commit()
                return
            if task.status != TaskStatus.QUEUED.value or task.pause_requested:
                # A task paused while waiting must not silently resume.
                return
            task.status = TaskStatus.RUNNING.value
            task.pause_requested = False
            task.started_at = datetime.now(timezone.utc)
            task.finished_at = None
            task.error_message = None
            db.commit()
            history_entry_id = begin_task_history(db, task)
            # Translation stages are asynchronous, so give the worker thread a
            # private event loop while keeping the web server loop responsive.
            processor = PipelineProcessor(db)
            asyncio.run(processor.execute(task_id))
            finish_task_history(db, task_id, history_entry_id)
            history_entry_id = None
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
            logger.exception("Processing task %s failed", task_id)
            # A poisoned transaction must never be reused to record its failure.
            terminal_persisted = False
            outcome = TaskManager._persist_failure(task_id, str(exc))
            terminal_persisted = True
            return outcome
        finally:
            try:
                if history_entry_id is not None and terminal_persisted:
                    finish_task_history(db, task_id, history_entry_id)
            finally:
                db.close()
        return task.status if task is not None else None

    @staticmethod
    def _persist_failure(task_id: str, error: str) -> str | None:
        with SessionLocal() as db:
            def save() -> str | None:
                task = db.get(ProcessingTask, task_id)
                if task is None:
                    return None
                if task.status in {TaskStatus.COMPLETED.value, TaskStatus.FAILED.value, TaskStatus.PAUSED.value}:
                    return task.status
                if task.status in {TaskStatus.CANCELLING.value, TaskStatus.CANCELLED.value}:
                    if task.page:
                        task.page.status = PipelineProcessor.stable_status_before(task.current_stage, task.page.status)
                        task.page.current_stage = None
                        task.page.error_message = None
                    TaskManager._finish_cancelled_task(task)
                else:
                    task.status = TaskStatus.FAILED.value
                    task.error_message = error
                    task.message = "Failed"
                    task.current_stage = None
                    task.finished_at = datetime.now(timezone.utc)
                    if task.page:
                        task.page.status = "FAILED"
                        task.page.error_message = error
                        task.page.current_stage = None
                return task.status

            return retry_write(db, save)

    @staticmethod
    def _recover_failed_task(task_id: str, error: str) -> None:
        from app.models import EditorHistoryEntry
        from app.services.editor_history import finish_task_history

        TaskManager._persist_failure(task_id, error)
        with SessionLocal() as db:
            entry_id = db.scalar(select(EditorHistoryEntry.id).where(EditorHistoryEntry.task_id == task_id))
            finish_task_history(db, task_id, entry_id)

    def terminal_error(self, task_id: str) -> str | None:
        with self.lock:
            return self.terminal_errors.get(task_id)

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
            from app.services.editor_history import recover_task_history
            recover_task_history(db)
        for task in queued:
            self.dispatch(task.id)


task_manager = TaskManager()

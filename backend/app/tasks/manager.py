from __future__ import annotations

import asyncio
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import case, select, update

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.transactions import is_retryable_write, retry_write
from app.models import ProcessingTask, StageCheckpoint, TaskExecutionRun, TaskWorkflow
from app.models.enums import TaskStatus
from app.pipeline.processor import PipelineProcessor, TaskCancelled, TaskPaused
from app.services.execution import execution_runtime
from app.tasks.admission import PageAdmission

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
    dependency_task_id: str | None = None
    project_id: str = ""
    interactive: bool = True


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
        self.dependency_events: dict[str, asyncio.Event] = {}
        self.admission = PageAdmission()

    def start(self) -> None:
        if get_settings().maintenance_mode:
            return
        self.loop = asyncio.get_running_loop()
        # Test clients and reloads may create a fresh event loop.
        self.page_locks = {}
        self.dependency_events = {}
        self.admission = PageAdmission()
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
        if get_settings().maintenance_mode:
            return
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
            dependency = task.dependency_task_id
            project_id = task.project_id or "unassigned"
            interactive = not bool(task.batch_id or (task.payload or {}).get("batch_submission"))
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
            attempt.dependency_task_id = dependency
            attempt.project_id,attempt.interactive = project_id,interactive
            attempt.units = max(.25, pixels / 1_000_000)
            self.terminal_errors.pop(task_id, None)
            self.attempts[task_id] = attempt
            self.pending.add(task_id)
        self.loop.call_soon_threadsafe(self._schedule, task_id, attempt)
        self.notify_dependency(task_id)

    def notify_dependency(self, task_id: str) -> None:
        def notify():
            event = self.dependency_events.get(task_id)
            if event is not None:
                event.set()
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(notify)

    def promote(self, task_id: str) -> None:
        """Explicit resume/retry joins the bounded interactive priority lane."""
        with self.lock:
            attempt = self.attempts.get(task_id)
            if attempt is None:
                return
            attempt.interactive = True

        def promote_ready():
            with self.lock:
                if self.attempts.get(task_id) is attempt:
                    self.admission.promote(f"{task_id}:{id(attempt)}")
        if self.loop and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(promote_ready)

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
        # Page ownership spans dependency waits; compute permits and worker
        # threads do not. Another edit for this page cannot overtake the task.
        page_lock = self.page_locks.setdefault(attempt.page_id,asyncio.Lock())
        async with page_lock:
            while not self.stopping and not attempt.cancelled:
                outcome = await self._run_attempt(task_id,attempt)
                if outcome != TaskStatus.QUEUED.value or not attempt.dependency_task_id:
                    return

    async def _run_attempt(self, task_id: str, attempt: _TaskAttempt) -> str | None:
        readiness = "ready"
        if attempt.dependency_task_id:
            from app.services.batch_context import dependency_admission
            changed = self.dependency_events.setdefault(attempt.dependency_task_id, asyncio.Event())
            while not self.stopping and not attempt.cancelled:
                changed.clear()
                try:
                    readiness = await asyncio.to_thread(dependency_admission, task_id)
                except Exception as error:
                    await asyncio.to_thread(self._persist_failure, task_id, str(error))
                    return
                if readiness in {"ready","repair"}:
                    break
                if readiness != "waiting":
                    return
                # Stage completion wakes only its dependants. The timeout is
                # recovery for out-of-process state changes, not per-page busy
                # polling of hundreds of waiting task rows.
                try:
                    await asyncio.wait_for(changed.wait(), timeout=30)
                except TimeoutError:
                    pass
            if self.stopping or attempt.cancelled:
                return
        if not self.stopping and not attempt.cancelled:
            runtime = execution_runtime()
            token = f"{task_id}:{id(attempt)}"
            # Ready continuations cannot sit behind hundreds of native-only
            # preparations; the fair queue still caps priority bursts at three.
            priority = attempt.interactive or bool(attempt.dependency_task_id and readiness == "ready")
            self.admission.add(token,attempt.project_id,interactive=priority)
            try:
                while not self.admission.claim(token,lambda: runtime.pages.try_acquire(attempt.estimate)):
                    if self.stopping or attempt.cancelled:
                        return
                    await asyncio.sleep(.1)
            finally:
                self.admission.remove(token)
            started = time.monotonic()
            failed = True
            outcome = None
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
                        if self.stopping or not is_retryable_write(recovery_error):
                            break
                        await asyncio.sleep(1)
            finally:
                kind = attempt.kind
                if attempt.dependency_task_id:
                    kind += ":repair_preparation" if readiness == "repair" else ":dependency_continuation"
                runtime.pages.release(time.monotonic() - started, attempt.estimate, kind=kind, units=attempt.units, failed=failed)
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
            return outcome

    def _task_finished(
        self,
        task_id: str,
        attempt: _TaskAttempt,
        wrapper: asyncio.Task[None],
    ) -> None:
        self.notify_dependency(task_id)
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
            if attempt.dependency_task_id and not any(item.dependency_task_id == attempt.dependency_task_id for item in self.attempts.values()):
                self.dependency_events.pop(attempt.dependency_task_id, None)

    @staticmethod
    def _run_blocking(task_id: str) -> str | None:
        from app.services.task_metrics import record_task_execution

        with record_task_execution(task_id):
            return TaskManager._execute_task(task_id)

    @staticmethod
    def _execute_task(task_id: str) -> str | None:
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
            def claim():
                db.refresh(task, with_for_update=True)
                if task.status != TaskStatus.QUEUED.value or task.pause_requested:
                    return False
                task.status = TaskStatus.RUNNING.value
                task.execution_generation += 1
                task.pause_requested = False
                task.started_at = datetime.now(timezone.utc)
                task.finished_at = None
                task.error_message = None
                task.error_kind, task.waiting_reason, task.active_stages = None, None, []
                return True
            if not retry_write(db, claim):
                return task.status
            history_entry_id = begin_task_history(db, task)
            from app.pipeline.workflow import PageWorkflow, prepare_target_geometry, supports_checkpoint_pipeline
            if supports_checkpoint_pipeline(task):
                if history_entry_id is None:
                    raise ValueError("Workflow requires an undo journal")
                db.rollback()
                prepare_target_geometry(task_id,history_entry_id)
                outcome = PageWorkflow(task_id, history_entry_id).execute()
                if outcome == TaskStatus.QUEUED.value:
                    # No published fields changed. The common finally block
                    # discards this preparation's pending journal; the eventual
                    # publication creates exactly one final undo entry.
                    return outcome
                history_entry_id = None  # Already closed in the publication transaction.
                return TaskStatus.COMPLETED.value
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
            elif task and task.status == TaskStatus.PAUSED.value:
                task.active_stages, task.waiting_reason = [], None
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
                    if task.page and not task.batch_id and not TaskManager._has_workflow(db, task.id):
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
                    from app.services.checkpoints import unknown_cloud_outcomes
                    task.error_kind = "unknown_cloud_outcome" if unknown_cloud_outcomes(db, task_id) else "processing_failed"
                    task.waiting_reason, task.active_stages = None, []
                    if task.page and not task.batch_id and not TaskManager._has_workflow(db, task.id):
                        task.page.status = "FAILED"
                        task.page.error_message = error
                        task.page.current_stage = None
                return task.status

            return retry_write(db, save)

    @staticmethod
    def _has_workflow(db, task_id):
        from app.models import TaskWorkflow
        return db.scalar(select(TaskWorkflow.id).where(TaskWorkflow.task_id == task_id).limit(1)) is not None

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
        task.error_kind, task.waiting_reason, task.active_stages = None, None, []
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
        if get_settings().maintenance_mode:
            return
        # Recovery may also be invoked while this manager is already running.
        # Only orphaned work belongs to the old process, never a live attempt.
        with self.lock:
            owned = tuple(self.attempts)
        with SessionLocal() as db:
            queued_ids = retry_write(db, lambda: self._recover_orphaned(db, owned))
            from app.services.editor_history import recover_task_history
            recover_task_history(db)
        # Persist plain IDs before closing the session/history reconciliation.
        for task_id in queued_ids:
            self.dispatch(task_id)

    def _recover_orphaned(self, db, owned: tuple[str, ...]) -> list[str]:
        # Checkpoint writers lock task -> checkpoint. Take task locks first in
        # stable order too, rather than checkpoint -> task (a PostgreSQL cycle).
        interrupted = list(db.scalars(select(ProcessingTask).where(
            ProcessingTask.status.in_([TaskStatus.RUNNING.value, TaskStatus.CANCELLING.value]),
            ProcessingTask.id.not_in(owned),
        ).order_by(ProcessingTask.id).with_for_update()).all())
        db.execute(update(StageCheckpoint).where(
            StageCheckpoint.status == "RUNNING",
            StageCheckpoint.workflow_id.in_(select(TaskWorkflow.id).where(TaskWorkflow.task_id.not_in(owned))),
        ).values(
            status=case((StageCheckpoint.cloud_request.is_(True), "UNKNOWN"), else_="INTERRUPTED"),
            error_kind=case((StageCheckpoint.cloud_request.is_(True), "unknown_cloud_outcome"), else_="interrupted"),
            finished_at=datetime.now(timezone.utc),
        ))
        db.execute(update(TaskExecutionRun).where(
            TaskExecutionRun.finished_at.is_(None),
            TaskExecutionRun.task_id.not_in(owned),
        ).values(status="INTERRUPTED", finished_at=datetime.now(timezone.utc)))
        for task in interrupted:
            if task.status == TaskStatus.CANCELLING.value:
                interrupted_stage = task.current_stage
                self._finish_cancelled_task(task)
                if task.page and not task.batch_id and not self._has_workflow(db, task.id):
                    task.page.current_stage = None
                    task.page.error_message = None
                    task.page.status = PipelineProcessor.stable_status_before(interrupted_stage, task.page.status)
            else:
                task.status = TaskStatus.FAILED.value
                task.error_message = "Application stopped while this in-process task was running; retry is available"
                task.message = "Interrupted"
                task.current_stage = None
                task.finished_at = datetime.now(timezone.utc)
                task.error_kind, task.waiting_reason, task.active_stages = "interrupted", None, []
                if task.page and not task.batch_id and not self._has_workflow(db, task.id):
                    task.page.status = "FAILED"
                    task.page.error_message = task.error_message
                    task.page.current_stage = None

        # Reconcile pages left stale by older versions of interruption recovery.
        stale_interrupted = list(db.scalars(select(ProcessingTask).where(
            ProcessingTask.status == TaskStatus.FAILED.value,
            ProcessingTask.message == "Interrupted",
            ProcessingTask.id.not_in(owned),
        )).all())
        for task in stale_interrupted:
            if task.page and task.page.current_stage and not task.batch_id and not self._has_workflow(db, task.id):
                task.page.status = "FAILED"
                task.page.error_message = task.error_message
                task.page.current_stage = None
        return list(db.scalars(select(ProcessingTask.id).where(ProcessingTask.status == TaskStatus.QUEUED.value)).all())


task_manager = TaskManager()

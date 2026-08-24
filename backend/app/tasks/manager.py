from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import ProcessingTask
from app.models.enums import TaskStatus
from app.pipeline.processor import PipelineProcessor, TaskPaused

logger = logging.getLogger(__name__)


class TaskManager:
    """In-process development queue with an API compatible path to external workers."""

    def __init__(self) -> None:
        self.running: dict[str, asyncio.Task[None]] = {}
        self.semaphore = asyncio.Semaphore(get_settings().task_concurrency)
        self.loop: asyncio.AbstractEventLoop | None = None
        self.pending: set[str] = set()
        self.lock = threading.Lock()

    def start(self) -> None:
        self.loop = asyncio.get_running_loop()

    def dispatch(self, task_id: str) -> None:
        if self.loop is None:
            raise RuntimeError("Task manager has not been started by the application lifespan")
        with self.lock:
            if task_id in self.pending:
                return
            self.pending.add(task_id)
        self.loop.call_soon_threadsafe(self._schedule, task_id)

    def _schedule(self, task_id: str) -> None:
        with self.lock:
            self.pending.discard(task_id)
            current = self.running.get(task_id)
            if current and not current.done():
                return
        task = asyncio.create_task(self._run(task_id), name=f"mangaflow-{task_id}")
        self.running[task_id] = task
        task.add_done_callback(lambda _: self.running.pop(task_id, None))

    async def _run(self, task_id: str) -> None:
        async with self.semaphore:
            # Model initialization and inference are CPU-bound synchronous work.
            # Running the pipeline coroutine directly on Uvicorn's event loop
            # prevents even the initial 202 response and task polling from being
            # served until inference finishes, which surfaces as an Nginx 504.
            await asyncio.to_thread(self._run_blocking, task_id)

    @staticmethod
    def _run_blocking(task_id: str) -> None:
        db = SessionLocal()
        try:
            task = db.get(ProcessingTask, task_id)
            if task is None or task.status == TaskStatus.CANCELLED.value:
                return
            task.status = TaskStatus.RUNNING.value
            task.pause_requested = False
            task.started_at = datetime.now(timezone.utc)
            task.error_message = None
            db.commit()
            # Translation stages are asynchronous, so give the worker thread a
            # private event loop while keeping the web server loop responsive.
            asyncio.run(PipelineProcessor(db).execute(task_id))
            db.refresh(task)
            if task.status not in {TaskStatus.PAUSED.value, TaskStatus.CANCELLED.value}:
                task.status = TaskStatus.COMPLETED.value
                task.progress = 1.0
                task.message = "Completed"
                task.finished_at = datetime.now(timezone.utc)
                db.commit()
        except TaskPaused:
            pass
        except Exception as exc:
            logger.exception("Processing task %s failed", task_id)
            task = db.get(ProcessingTask, task_id)
            if task and task.status != TaskStatus.CANCELLED.value:
                task.status = TaskStatus.FAILED.value
                task.error_message = str(exc)
                task.message = "Failed"
                task.finished_at = datetime.now(timezone.utc)
                if task.page:
                    task.page.status = "FAILED"
                    task.page.error_message = str(exc)
                    task.page.current_stage = None
                db.commit()
        finally:
            db.close()

    def cancel(self, task_id: str) -> None:
        running = self.running.get(task_id)
        if running and not running.done() and self.loop:
            self.loop.call_soon_threadsafe(running.cancel)

    def recover_interrupted(self) -> None:
        with SessionLocal() as db:
            interrupted = list(
                db.scalars(select(ProcessingTask).where(ProcessingTask.status == TaskStatus.RUNNING.value)).all()
            )
            for task in interrupted:
                task.status = TaskStatus.FAILED.value
                task.error_message = "Application stopped while this in-process task was running; retry is available"
                task.message = "Interrupted"
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

"""Durable stage results fenced by the task's persisted execution generation."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from app.core.serialization import file_digest
from app.core.transactions import retry_write
from app.models import ProcessingTask, StageCheckpoint, TaskWorkflow
from app.services.workflow_state import fingerprint, page_version
from sqlalchemy import func, select

STAGES = {"detection", "ocr", "translation", "mask", "inpainting", "rendering"}


class StaleExecution(RuntimeError):
    pass


class StageBusy(RuntimeError):
    pass


class UnknownCloudOutcome(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkflowLease:
    id: str
    task_id: str
    generation: int
    page_version: str


@dataclass(frozen=True)
class StageTicket:
    id: str
    workflow: WorkflowLease
    stage: str
    attempt: int
    fingerprint: str


class CheckpointStore:
    def __init__(self, sessions, storage):
        self.sessions, self.storage = sessions, storage

    @staticmethod
    def _task(db, task_id: str, generation: int, *, terminal: bool = False):
        task = db.scalar(select(ProcessingTask).where(ProcessingTask.id == task_id).with_for_update())
        if task is None or task.execution_generation != generation or (not terminal and task.status != "RUNNING"):
            raise StaleExecution("Task attempt is no longer allowed to publish results")
        return task

    def _workflow(self, db, lease: WorkflowLease, *, terminal: bool = False):
        self._task(db, lease.task_id, lease.generation, terminal=terminal)
        workflow = db.get(TaskWorkflow, lease.id)
        if workflow is None or workflow.generation != lease.generation or workflow.status != "RUNNING":
            raise StaleExecution("Workflow attempt is no longer active")
        return workflow

    def begin(self, task_id: str, generation: int, state: dict, policy: dict) -> WorkflowLease:
        version, request = page_version(state), fingerprint(policy)
        # JSON copies/hashing happen before the short DB transaction.
        state, policy = deepcopy(state), deepcopy(policy)
        with self.sessions() as db:
            def save():
                self._task(db, task_id, generation)
                prior = db.scalar(select(TaskWorkflow).where(
                    TaskWorkflow.task_id == task_id, TaskWorkflow.input_fingerprint == version,
                    TaskWorkflow.request_fingerprint == request, TaskWorkflow.status != "PUBLISHED",
                ).order_by(TaskWorkflow.created_at.desc()).limit(1))
                for other in db.scalars(select(TaskWorkflow).where(
                    TaskWorkflow.task_id == task_id, TaskWorkflow.status == "RUNNING",
                )):
                    if prior is None or other.id != prior.id:
                        other.status = "SUPERSEDED"
                if prior is None:
                    prior = TaskWorkflow(id=str(uuid4()), task_id=task_id, generation=generation,
                        input_fingerprint=version, request_fingerprint=request, input_state=state, policy=policy)
                    db.add(prior)
                else:
                    if prior.generation != generation:
                        for checkpoint in db.scalars(select(StageCheckpoint).where(
                            StageCheckpoint.workflow_id == prior.id, StageCheckpoint.status == "RUNNING",
                        )):
                            checkpoint.status = "UNKNOWN" if checkpoint.cloud_request else "INTERRUPTED"
                            checkpoint.error_kind = "unknown_cloud_outcome" if checkpoint.cloud_request else "interrupted"
                            checkpoint.finished_at = datetime.now(timezone.utc)
                    prior.generation = generation
                    prior.status = "RUNNING"
                return WorkflowLease(prior.id, task_id, generation, version)
            return retry_write(db, save)

    def initial_state(self, lease: WorkflowLease) -> dict:
        with self.sessions() as db:
            row = db.get(TaskWorkflow, lease.id)
            if row is None:
                raise StaleExecution("Workflow no longer exists")
            return deepcopy(row.input_state)

    def directory(self, ticket: StageTicket) -> Path:
        with self.sessions() as db:
            task = db.get(ProcessingTask, ticket.workflow.task_id)
            if task is None or not task.project_id or not task.image_id:
                raise StaleExecution("Workflow page no longer exists")
            directory = self.storage.page_dir(task.project_id, task.image_id)
        return directory / "workflows" / ticket.workflow.id / ticket.id

    def claim(self, lease: WorkflowLease, stage: str, input_fingerprint: str, *, cloud: bool = False) -> StageTicket:
        if stage not in STAGES:
            raise ValueError("Unknown workflow stage")
        with self.sessions() as db:
            def save():
                self._workflow(db, lease)
                previous = list(db.scalars(select(StageCheckpoint).where(
                    StageCheckpoint.workflow_id == lease.id, StageCheckpoint.stage == stage,
                    StageCheckpoint.input_fingerprint == input_fingerprint,
                )))
                if any(row.status == "UNKNOWN" for row in previous):
                    raise UnknownCloudOutcome("上次云端请求结果不确定，继续可能重复计费；请确认后重试")
                if any(row.status in {"RUNNING", "SUCCEEDED"} for row in previous):
                    raise StageBusy("Stage already has an active or reusable result")
                attempt = (db.scalar(select(func.max(StageCheckpoint.attempt)).where(
                    StageCheckpoint.workflow_id == lease.id, StageCheckpoint.stage == stage,
                )) or 0) + 1
                identifier = str(uuid4())
                db.add(StageCheckpoint(id=identifier, workflow_id=lease.id, stage=stage, attempt=attempt,
                    generation=lease.generation, input_fingerprint=input_fingerprint, cloud_request=cloud))
                return StageTicket(identifier, lease, stage, attempt, input_fingerprint)
            return retry_write(db, save)

    def successful(self, lease: WorkflowLease, stage: str, input_fingerprint: str) -> dict | None:
        with self.sessions() as db:
            self._workflow(db, lease)
            row = db.scalar(select(StageCheckpoint).where(
                StageCheckpoint.workflow_id == lease.id, StageCheckpoint.stage == stage,
                StageCheckpoint.input_fingerprint == input_fingerprint, StageCheckpoint.status == "SUCCEEDED",
            ).order_by(StageCheckpoint.attempt.desc()).limit(1))
            if row is None:
                return None
            identifier, result, artifacts = row.id, deepcopy(row.result), deepcopy(row.artifacts)
        # No database transaction is held during file hashing.
        if self.validate_artifacts(artifacts):
            return result
        with self.sessions() as db:
            def invalidate():
                self._workflow(db, lease)
                row = db.get(StageCheckpoint, identifier)
                if row and row.status == "SUCCEEDED":
                    row.status, row.error_kind = "INVALID", "artifact_changed"
            retry_write(db, invalidate)
        return None

    def artifact_manifest(self, ticket: StageTicket) -> dict:
        directory = self.directory(ticket).resolve()
        result = {}
        if directory.is_dir():
            for path in directory.rglob("*"):
                if path.is_symlink() or not path.resolve().is_relative_to(directory):
                    raise ValueError("Workflow output must not escape its immutable directory")
                if path.is_file():
                    result[self.storage.relative(path)] = {"bytes": path.stat().st_size, "sha256": file_digest(path)}
        return result

    def validate_artifacts(self, artifacts: dict) -> bool:
        try:
            return all((path := self.storage.absolute(relative)).is_file()
                       and path.stat().st_size == expected["bytes"] and file_digest(path) == expected["sha256"]
                       for relative, expected in artifacts.items())
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def validate_proofs(self, lease: WorkflowLease, proofs: dict[str, str]) -> list[str]:
        identifiers, artifacts = [], {}
        with self.sessions() as db:
            self._workflow(db, lease)
            for stage, expected in proofs.items():
                row = db.scalar(select(StageCheckpoint).where(
                    StageCheckpoint.workflow_id == lease.id, StageCheckpoint.stage == stage,
                    StageCheckpoint.input_fingerprint == expected, StageCheckpoint.status == "SUCCEEDED",
                ).order_by(StageCheckpoint.attempt.desc()).limit(1))
                if row is None:
                    raise StaleExecution("A required stage result is missing")
                identifiers.append(row.id)
                artifacts.update(deepcopy(row.artifacts))
        if not self.validate_artifacts(artifacts):
            raise StaleExecution("Workflow artifact validation failed")
        return identifiers

    def succeed(self, ticket: StageTicket, result: dict) -> None:
        artifacts, result = self.artifact_manifest(ticket), deepcopy(result)
        with self.sessions() as db:
            def save():
                self._workflow(db, ticket.workflow, terminal=True)
                # Pausing drains in-flight stages first. Keep their expensive
                # results, but cancellation and superseded workers cannot save.
                task = db.get(ProcessingTask, ticket.workflow.task_id)
                if task.status not in {"RUNNING", "PAUSED"}:
                    raise StaleExecution("Stopped task cannot save stage results")
                row = db.get(StageCheckpoint, ticket.id)
                if row is None or row.status != "RUNNING" or row.generation != ticket.workflow.generation:
                    raise StaleExecution("Stage attempt is no longer active")
                row.status, row.result, row.artifacts = "SUCCEEDED", result, artifacts
                row.finished_at = datetime.now(timezone.utc)
            retry_write(db, save)

    def fail(self, ticket: StageTicket, *, uncertain: bool = False, kind: str = "stage_failed") -> None:
        with self.sessions() as db:
            def save():
                self._workflow(db, ticket.workflow, terminal=True)
                row = db.get(StageCheckpoint, ticket.id)
                if row is not None and row.status == "RUNNING" and row.generation == ticket.workflow.generation:
                    row.status = "UNKNOWN" if uncertain and row.cloud_request else "FAILED"
                    # UNKNOWN status still requires explicit acknowledgement.
                    # Preserve safe provider diagnoses rather than discarding
                    # whether the received response was empty/refused/truncated.
                    row.error_kind = (kind if kind.startswith("translation_") else "unknown_cloud_outcome") if row.status == "UNKNOWN" else kind
                    row.finished_at = datetime.now(timezone.utc)
            retry_write(db, save)

    def acknowledged_cloud(self, lease: WorkflowLease, stage: str, key: str) -> bool:
        with self.sessions() as db:
            return db.scalar(select(StageCheckpoint.id).where(
                StageCheckpoint.workflow_id == lease.id, StageCheckpoint.stage == stage,
                StageCheckpoint.input_fingerprint == key, StageCheckpoint.status == "ACKNOWLEDGED",
            ).limit(1)) is not None


def unknown_cloud_outcomes(db, task_id: str) -> list[StageCheckpoint]:
    return list(db.scalars(select(StageCheckpoint).join(TaskWorkflow).where(
        TaskWorkflow.task_id == task_id, StageCheckpoint.status == "UNKNOWN",
    )))

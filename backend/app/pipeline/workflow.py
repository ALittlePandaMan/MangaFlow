"""Checkpointed page operations with one atomic, reversible publication join."""
from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy

from sqlalchemy import select, update

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.core.transactions import retry_write
from app.models import EditorHistoryEntry, ImagePage, ModelConfig, ProcessingTask, Project, TaskWorkflow, TextRegion
from app.pipeline.processor import PipelineProcessor, TaskCancelled, TaskPaused
from app.pipeline.staged import run_stage
from app.services.batch_context import POLICY_SETTINGS, batch_policy
from app.services.checkpoints import CheckpointStore, StaleExecution
from app.services.cloud_results import CloudResultCache, current_cloud_results
from app.services.database_transfer import file_digest
from app.services.execution import execution_runtime
from app.services.task_metrics import flush_task_timings
from app.services.translation.context import page_context
from app.services.workflow_publish import publish_workflow
from app.services.workflow_state import (
    apply_delta,
    capture_page,
    fingerprint,
    merge_branches,
    page_version,
    record,
    requested_stages,
)
from app.storage import get_storage


def supports_checkpoint_pipeline(task) -> bool:
    payload = task.payload or {}
    try:
        stages = requested_stages(payload)
    except ValueError:
        return False
    # Whole-page replacement cannot target a region whose deletion would also
    # cascade-delete the task that owns this workflow.
    if "detection" in stages and task.region_id:
        return False
    if get_settings().checkpoint_pipeline_enabled:
        return True
    # Disabling rollout affects NEW tasks, never discards paid checkpoints of
    # paused/failed tasks by silently routing their retry through the old path.
    with SessionLocal() as db:
        return db.scalar(select(TaskWorkflow.id).where(TaskWorkflow.task_id == task.id).limit(1)) is not None


def prepare_target_geometry(task_id: str, history_entry_id: int) -> None:
    """Accept explicit target-frame input before fallible repair, as before.

    This is a user-requested geometry edit, not a computed model result. Keep
    it reversible in the SAME task journal even if repair fails. No files or
    model work runs in this small transaction; source-view requests do nothing.
    """
    from app.schemas.domain import RegionUpdate
    from app.services.regions import synchronize_source_geometry_before_repair

    with SessionLocal() as db:
        task = db.get(ProcessingTask,task_id)
        if task is None:
            raise StaleExecution("Task no longer exists")
        request = deepcopy(task.payload or {})
        if request.get("options",{}).get("repair_geometry","source")!="translated" or "inpainting" not in requested_stages(request):
            return
        project_id,page_id,generation = task.project_id,task.image_id,task.execution_generation
        ids = set(request.get("options",{}).get("region_ids",[]))
        if task.region_id:
            ids = {task.region_id}

        def save():
            db.execute(update(Project).where(Project.id==project_id).values(updated_at=Project.updated_at))
            current = db.scalar(select(ProcessingTask).where(ProcessingTask.id==task_id).with_for_update())
            if current is None or current.execution_generation!=generation or current.status!="RUNNING":
                raise StaleExecution("Stopped task cannot accept target geometry")
            if current.pause_requested:
                current.status,current.current_stage,current.active_stages = "PAUSED",None,[]
                current.message = "Paused before accepting target geometry"
                return "PAUSED"
            page = db.scalar(select(ImagePage).where(ImagePage.id==page_id).with_for_update())
            entry = db.get(EditorHistoryEntry,history_entry_id)
            if page is None or entry is None or not entry.pending or entry.task_id!=task_id:
                raise StaleExecution("Target geometry journal is no longer pending")
            query = select(TextRegion).where(TextRegion.image_id==page_id)
            if ids:
                query = query.where(TextRegion.id.in_(ids))
            regions = list(db.scalars(query))
            if ids and {region.id for region in regions}!=ids:
                raise ValueError("Selected target regions no longer exist")
            # Validate ALL inputs before applying any of the frame changes.
            for region in regions:
                box,polygon = region.translated_bbox or region.bbox,region.translated_polygon or region.polygon
                if len(box)!=4 or len(polygon)<3:
                    raise ValueError("Invalid target frame")
                RegionUpdate(bbox=box,polygon=polygon)
            changed = False
            for region in regions:
                changed |= synchronize_source_geometry_before_repair(db,region)
            if changed:
                entry.after = {**entry.after,"_prepared_target_geometry":True}
        if retry_write(db,save)=="PAUSED":
            raise TaskPaused("Task paused before accepting target geometry")


def snapshot_request(task_id: str):
    settings, storage = get_settings(), get_storage()
    with SessionLocal() as db:
        task = db.get(ProcessingTask, task_id)
        if task is None or task.status != "RUNNING":
            raise StaleExecution("Task is no longer running")
        generation, page_id = task.execution_generation, task.image_id
        request = {**deepcopy(task.payload), "region_id": task.region_id}
        if request.get("start_stage") == "full":
            request["start_stage"] = "detection"
        if request.get("end_stage") == "full":
            request["end_stage"] = "rendering"
        if request.get("options", {}).get("repair_geometry", "source") not in {"source", "translated"}:
            raise ValueError("Invalid repair geometry: expected source or translated")
        frozen = batch_policy(db, task)
        if frozen is None:
            processor = PipelineProcessor(db)
            source_state = capture_page(db,page_id)
            scope = set(request.get("options",{}).get("region_ids",[]))
            if request.get("region_id"):
                scope = {request["region_id"]}
            explicit_force = bool(scope and request.get("force"))
            needs_ocr = any((not scope or row["id"] in scope) and not row["source_text"].strip()
                and (not row["locked"] or explicit_force) and (request.get("force") or not row["translated_text"].strip())
                for row in source_state["regions"].values())
            for kind in requested_stages(request):
                if kind == "mask" or (kind == "ocr" and request.get("start_stage")=="translation" and not needs_ocr):
                    continue
                processor._provider(kind, None)  # Provision once before freezing config, never inside private workers.
        initial = capture_page(db, page_id)
        page = db.get(ImagePage, page_id)
        previous = None if frozen else db.scalar(select(ImagePage).where(ImagePage.project_id == page.project_id,
                            ImagePage.order_index < page.order_index).order_by(ImagePage.order_index.desc()).limit(1))
        policy = {"version": 1, "request": request,
                  "project": {key: initial["project"][key] for key in ("source_language", "target_language", "translation_context", "settings")},
                  "models": frozen["models"] if frozen else [record(row) for row in db.scalars(select(ModelConfig).where(ModelConfig.enabled.is_(True)).order_by(ModelConfig.id))],
                  "settings": {key: getattr(settings, key) for key in POLICY_SETTINGS},
                  "previous_page": None if previous is None else {"page_id": previous.id, "regions": page_context(previous.regions, character_budget=4000)},
                  "failed_tasks": [record(row) for row in db.scalars(select(ProcessingTask).where(
                      ProcessingTask.image_id == page_id, ProcessingTask.status == "FAILED", ProcessingTask.id != task_id))]}
        if frozen is not None:
            policy.update(frozen)
            initial["project"].update(deepcopy(frozen["project"]))
    # Immutable file inputs are also fenced, not just database paths. No SQL
    # connection is held while hashing source pixels or the user's masks.
    paths = {initial["page"]["original_path"]}
    paths.update(row["pixel_mask_path"] for row in initial["regions"].values() if row["pixel_mask_path"])
    if initial["page"]["clean_path"]:
        paths.add(initial["page"]["clean_path"])
    policy["input_assets"] = {path: file_digest(storage.absolute(path)) for path in sorted(paths)}
    # Previous-page progress and old task diagnostics are not new user input.
    # Keep their original snapshots on retry; otherwise a sibling page finishing
    # would silently invalidate an already paid-for translation checkpoint.
    with SessionLocal() as db:
        previous_workflows = db.scalars(select(TaskWorkflow).where(
            TaskWorkflow.task_id == task_id, TaskWorkflow.input_fingerprint == page_version(initial),
            TaskWorkflow.status != "PUBLISHED",
        ).order_by(TaskWorkflow.created_at.desc()))
        changing_context = {"previous_page", "failed_tasks"}
        for previous_workflow in previous_workflows:
            old_policy = previous_workflow.policy
            if fingerprint({k: v for k, v in old_policy.items() if k not in changing_context}) == fingerprint(
                {k: v for k, v in policy.items() if k not in changing_context}
            ):
                policy = deepcopy(old_policy)
                break
    return generation, initial, policy


def cloud_stage(stage: str, policy: dict) -> bool:
    if stage not in {"ocr", "translation"}:
        return False
    # Missing-text OCR always uses the OCR default, not the translator override.
    requested = policy["request"].get("provider") if stage == policy["request"].get("start_stage") else None
    candidates = [row for row in policy["models"] if row["kind"] == stage
                  and (requested in {row["provider"], row["name"]} if requested else row["is_default"])]
    selected = max(candidates, key=lambda row: row["updated_at"], default=None)
    provider = selected["provider"] if selected else requested
    return provider in {"openai-compatible", "cloud-multimodal", "cloud-ocr"}


class PageWorkflow:
    def __init__(self, task_id: str, history_entry_id: int):
        self.task_id, self.history_entry_id = task_id, history_entry_id
        self.storage = get_storage()
        self.store = CheckpointStore(SessionLocal, self.storage)
        self.active, self.done, self.proofs = set(), set(), {}
        self.lock = threading.RLock()
        generation, state, policy = snapshot_request(task_id)
        self.lease = self.store.begin(task_id, generation, state, policy)
        self.initial = self.store.initial_state(self.lease)
        with SessionLocal() as db:
            self.policy = deepcopy(db.get(TaskWorkflow, self.lease.id).policy)
        self.stages = requested_stages(self.policy["request"])

    def check(self, *, allow_pause: bool = True):
        with SessionLocal() as db:
            task = db.get(ProcessingTask, self.task_id)
            if task is None or task.execution_generation != self.lease.generation or task.status in {"CANCELLING", "CANCELLED"}:
                raise TaskCancelled("Task cancelled")
            if allow_pause and (task.pause_requested or task.status == "PAUSED"):
                def pause():
                    current = db.get(ProcessingTask, self.task_id)
                    if current and current.execution_generation == self.lease.generation and current.status == "RUNNING":
                        current.status, current.message = "PAUSED", "Paused at stage checkpoint"
                retry_write(db, pause)
                raise TaskPaused("Paused at stage checkpoint")
            if task.status not in ({"RUNNING"} if allow_pause else {"RUNNING", "PAUSED"}):
                raise StaleExecution("Workflow attempt is no longer running")

    def report(self, stage: str, *, finished: bool = False):
        with self.lock:
            if finished:
                self.active.discard(stage)
                self.done.add(stage)
            else:
                self.active.add(stage)
            active, completed = sorted(self.active), len(self.done)
            with SessionLocal() as db:
                def save():
                    task = db.get(ProcessingTask, self.task_id)
                    if task and task.execution_generation == self.lease.generation and task.status == "RUNNING":
                        task.current_stage = active[0] if len(active) == 1 else "parallel" if active else None
                        task.active_stages = active
                        task.waiting_reason = None
                        task.message = "Running " + " + ".join(active) if active else "Stage results saved"
                        task.progress = min(.95, completed / len(self.stages))
                retry_write(db, save)

    def stage(self, stage: str, state: dict) -> dict:
        self.check()
        policy = self.policy
        if stage == "translation" and self.policy.get("batch",{}).get("dependency_task_id"):
            with SessionLocal() as db:
                task = db.get(ProcessingTask,self.task_id)
                if task is None or task.dependency_context is None:
                    raise ValueError("Previous-page translation is not ready")
                policy = {**self.policy,"previous_page":deepcopy(task.dependency_context)}
        # Paths/content/config and preceding values define exact reuse, never
        # stage name alone. Component timestamps are normalized by snapshots.
        key = fingerprint({"state": state, "policy": policy, "stage": stage})
        cached = self.store.successful(self.lease, stage, key)
        if cached is None:
            cloud = cloud_stage(stage, policy)
            ticket = self.store.claim(self.lease, stage, key, cloud=cloud)
            self.report(stage)
            started = time.monotonic()
            cache_token = None
            try:
                directory = self.store.directory(ticket)
                cache = CloudResultCache(self.storage.root, directory.parent / ".requests" / stage / key,
                    allow_invalid=not cloud or self.store.acknowledged_cloud(self.lease,stage,key)) if cloud or stage=="ocr" else None
                cache_token = current_cloud_results.set(cache)
                cached = run_stage(stage, state, policy, directory, self.storage,
                                   lambda: self.check(allow_pause=False),
                                   progress=lambda completed,total,message:self.progress(stage,completed,total,message))
                self.store.succeed(ticket, cached)
                if cache:
                    cache.discard_saved()
            except BaseException as error:
                try:
                    # Even a malformed paid response can raise ValueError.
                    # Never infer 'not sent' merely from the exception class.
                    self.store.fail(ticket, uncertain=cloud, kind=getattr(error, "error_kind", None) or type(error).__name__)
                except StaleExecution:
                    pass
                raise
            finally:
                if cache_token is not None:
                    current_cloud_results.reset(cache_token)
                execution_runtime().record_stage(self.task_id, stage, time.monotonic() - started)
                flush_task_timings()
        with self.lock:
            self.proofs[stage] = key
        if stage == "translation":
            from app.tasks import task_manager
            task_manager.notify_dependency(self.task_id)
        self.report(stage, finished=True)
        self.check()
        output = apply_delta(state, cached["changes"])
        output["ocr_attempted_ids"] = cached["ocr_attempted_ids"]
        return output

    def progress(self, stage, completed, total, message):
        with self.lock:
            with SessionLocal() as db:
                def save():
                    task = db.get(ProcessingTask,self.task_id)
                    if task and task.execution_generation==self.lease.generation and task.status=="RUNNING":
                        task.message = ("翻译前自动识别 · " if stage=="ocr" and self.policy["request"].get("start_stage")=="translation" else "")+str(message)
                        fraction = completed/max(1,total)
                        task.progress = min(.95,(len(self.done)+fraction)/len(self.stages))
                retry_write(db,save)

    def execute(self):
        request = self.policy["request"]
        combined = (request.get("end_stage") == "rendering" and "translation" in self.stages
                    and not request.get("region_id") and not request.get("options", {}).get("region_ids"))
        if not combined:
            result = self.initial
            for stage in self.stages:
                result = self.stage(stage, result)
            return self.publish(result)
        # Full-page requests share the same join as translation-to-rendering:
        # first produce stable detection/OCR inputs, then overlap independent
        # translation and source-frame repair. Never redetect in a sibling.
        base = self.initial
        for stage in self.stages[:self.stages.index("translation")]:
            base = self.stage(stage, base)

        def repair():
            return self.stage("inpainting", self.stage("mask", base))

        parallel = get_settings().parallel_repair_translation and self.policy["request"].get("options", {}).get("repair_geometry", "source") == "source"
        if parallel and self.policy.get("batch",{}).get("dependency_task_id"):
            with SessionLocal() as db:
                task = db.get(ProcessingTask,self.task_id)
                pending = task is not None and task.dependency_context is None
            if pending:
                repair()
                self.check()
                with SessionLocal() as db:
                    def wait_for_context():
                        task = db.scalar(select(ProcessingTask).where(ProcessingTask.id == self.task_id).with_for_update())
                        if task is None or task.execution_generation != self.lease.generation or task.status != "RUNNING":
                            raise StaleExecution("Task changed while saving prepared repair")
                        task.status,task.current_stage,task.active_stages = "QUEUED",None,[]
                        task.waiting_reason = "previous_page_translation"
                        task.message = "背景修复已保存，等待前页翻译（未占用处理名额）"
                    retry_write(db,wait_for_context)
                return "QUEUED"
        if parallel:
            # One extra orchestration thread/page, separate from the CPU pool;
            # models and CPU jobs still acquire their own bounded resources.
            with ThreadPoolExecutor(1, thread_name_prefix="mangaflow-translation") as pool:
                translated = pool.submit(copy_context().run, self.stage, "translation", base)
                repair_error = None
                try:
                    repaired = repair()
                except BaseException as error:
                    repair_error = error
                # Drain a sibling even on failure; it may save a reusable
                # successful checkpoint, but never publish independently.
                translation = translated.result()
                if repair_error is not None:
                    raise repair_error
            joined = merge_branches(base, translation, repaired)
        else:
            translated = self.stage("translation", base)
            joined = self.stage("inpainting", self.stage("mask", translated))
        result = self.stage("rendering", joined)
        return self.publish(result)

    def publish(self, result):
        self.check()
        for path, expected in self.policy["input_assets"].items():
            if file_digest(self.storage.absolute(path)) != expected:
                raise StaleExecution("Workflow source asset changed before publication")
        publish_workflow(self.store, self.lease, self.initial, result, self.history_entry_id, proofs=self.proofs)
        return "COMPLETED"

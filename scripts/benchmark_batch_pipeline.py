"""Prepare and audit an isolated copy of the exact historical 337-page batch.

Preparation is offline: it never starts a worker or sends a paid request. The
source is opened only through SQLite's read-only backup interface. Destination
database/data must be private, empty and distinct from the source deployment.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import psutil
from app.core.config import get_settings
from app.core.db_engine import create_database_engine
from app.core.migrations import upgrade_database
from app.models import EditorHistoryEntry, ImagePage, ModelConfig, ProcessingTask, TaskExecutionRun, TaskWorkflow
from app.services.database_transfer import backup_sqlite, copy_database, file_digest
from app.services.workflow_state import capture_page, fingerprint
from cryptography.fernet import Fernet
from sqlalchemy import func, select
from sqlalchemy.orm import Session

FONT_SUFFIXES = {".ttf",".otf",".ttc",".otc",".woff",".woff2"}


def references(value):
    if isinstance(value,str) and (value.startswith("projects/") or value.startswith("fonts/")):
        yield value
    elif isinstance(value,dict):
        for key,item in value.items():
            yield from references(key)
            yield from references(item)
    elif isinstance(value,list):
        for item in value:
            yield from references(item)


def frozen_inputs(engine, project_id):
    with Session(engine) as db:
        tasks = list(db.scalars(select(ProcessingTask).where(ProcessingTask.project_id==project_id)
                               .order_by(ProcessingTask.created_at,ProcessingTask.id)))
        tasks = [task for task in tasks if task.payload.get("start_stage")=="translation"
                 and task.payload.get("end_stage")=="rendering"]
        identifiers = {task.image_id for task in tasks}
        if len(tasks)!=337 or len(identifiers)!=337:
            raise ValueError("The source must identify the exact original 337-page translation/repair batch")
        pages = list(db.scalars(select(ImagePage).where(ImagePage.id.in_(identifiers))
                               .order_by(ImagePage.order_index,ImagePage.id)))
        states = [capture_page(db,page.id) for page in pages]
        if len(states)!=337 or any(not state["regions"] for state in states):
            raise ValueError("All 337 source pages must retain their detected text regions")
        statuses = {status:sum(task.status==status for task in tasks) for status in sorted({task.status for task in tasks})}
        return states, {"tasks":len(tasks),"statuses":statuses,
                        "created_from":min(task.created_at for task in tasks).isoformat(),
                        "created_to":max(task.created_at for task in tasks).isoformat()}


def prepare(args):
    settings = get_settings()
    source,root = args.source.resolve(),settings.data_dir.resolve()
    if (source==root or root.is_relative_to(source) or not args.output.resolve().is_relative_to(root)
            or not settings.database_url):
        raise ValueError("Use a separate data directory and an explicitly configured isolated database")
    if args.output.exists():
        raise ValueError("Use a fresh acceptance report directory; existing work is never replaced")
    args.output.mkdir(parents=True)
    snapshot = args.output/"source-snapshot.db"
    backup = backup_sqlite(source/"mangaflow.db",snapshot)
    original = create_database_engine(f"sqlite:///{snapshot}")
    target = create_database_engine(settings.database_url)
    try:
        upgrade_database(original)
        states,baseline = frozen_inputs(original,args.project)
        with Session(original) as db:
            active = db.scalar(select(func.count()).select_from(ProcessingTask).where(
                ProcessingTask.status.in_(["QUEUED","RUNNING","CANCELLING","PAUSED"])))
            pending = db.scalar(select(func.count()).select_from(EditorHistoryEntry).where(EditorHistoryEntry.pending.is_(True)))
            if active or pending:
                raise ValueError("Source snapshot contains active work; do not auto-start its copied jobs")
            cipher = Fernet((source/".secret_key").read_bytes())
            decrypted = 0
            models = []
            for row in db.scalars(select(ModelConfig)):
                if row.encrypted_api_key:
                    cipher.decrypt(row.encrypted_api_key.encode())
                    decrypted += 1
                if row.enabled and row.is_default:
                    models.append({"kind":row.kind,"provider":row.provider})
        paths = set(references(states))
        for directory in (source/"fonts",source/"projects"/args.project/"fonts"):
            if directory.is_dir():
                paths.update(path.relative_to(source).as_posix() for path in directory.rglob("*")
                             if path.is_file() and path.suffix.lower() in FONT_SUFFIXES)
        manifest,total = {},0
        for relative in sorted(paths):
            origin = (source/relative).resolve()
            destination = (root/relative).resolve()
            if not origin.is_relative_to(source) or not destination.is_relative_to(root) or not origin.is_file():
                raise ValueError("Acceptance input escapes its root or is missing")
            if destination.exists():
                raise ValueError("Acceptance data already exists; refusing to replace it")
            destination.parent.mkdir(parents=True,exist_ok=True)
            digest = file_digest(origin)
            shutil.copyfile(origin,destination)
            if file_digest(destination)!=digest:
                raise ValueError("Acceptance input copy differs from its source")
            manifest[relative] = {"sha256":digest,"bytes":origin.stat().st_size}
            total += origin.stat().st_size
        secret = root/".secret_key"
        if secret.exists():
            raise ValueError("Acceptance key already exists")
        shutil.copyfile(source/".secret_key",secret)
        secret.chmod(0o600)
        policy = source/"execution-policy.json"
        if policy.is_file():
            shutil.copyfile(policy,root/"execution-policy.json")
        transfer = copy_database(original,target)
        copied,_ = frozen_inputs(target,args.project)
        source_fingerprint = fingerprint(states)
        if fingerprint(copied)!=source_fingerprint:
            raise ValueError("Copied page state differs from the source")
        private = {"project_id":args.project,"page_ids":[state["page"]["id"] for state in states],
            "source_fingerprint":source_fingerprint,"assets":manifest}
        private_path = args.output/"private-inputs.json"
        private_path.write_text(json.dumps(private,ensure_ascii=False),encoding="utf-8")
        private_path.chmod(0o600)
        report = {"scope":"exact_original_337_pages_offline_preparation_no_paid_requests",
            "source_snapshot_bytes":backup["bytes"],"source_backup_sha256_before_private_upgrade":backup["sha256"],
            "prepared_snapshot_sha256":file_digest(snapshot),
            "source_fingerprint":source_fingerprint,"pages":len(states),"regions":sum(len(state["regions"]) for state in states),
            "assets":len(manifest),"asset_bytes":total,"decrypted_keys_verified":decrypted,
            "baseline":baseline,"default_providers":models,"database_transfer":transfer,
            "source_is_read_only":True,"paid_requests_started":False}
        (args.output/"preparation-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(json.dumps(report),flush=True)
    finally:
        original.dispose()
        target.dispose()


def write_report(path, value, *, exclusive=False):
    """Persist a small local evidence file; no task/request bodies are saved."""
    with path.open("x" if exclusive else "w",encoding="utf-8") as stream:
        json.dump(value,stream,ensure_ascii=False,indent=2)
        stream.flush()
        os.fsync(stream.fileno())


def preflight(args):
    if (args.output/"dispatch-started.json").exists():
        raise ValueError("A batch was already submitted; observe it instead of restarting preflight")
    private = json.loads((args.output/"private-inputs.json").read_text(encoding="utf-8"))
    root = get_settings().data_dir
    engine = create_database_engine(get_settings().resolved_database_url)
    try:
        copied,_ = frozen_inputs(engine,args.project)
        if fingerprint(copied)!=private["source_fingerprint"]:
            raise ValueError("Prepared inputs changed before dispatch")
        if not all(file_digest(root/relative)==value["sha256"] for relative,value in private["assets"].items()):
            raise ValueError("An isolated input asset changed before dispatch")
        report_path = args.output/"preparation-report.json"
        report = json.loads(report_path.read_text(encoding="utf-8"))
        if "source_snapshot_sha256" in report:
            report["source_backup_sha256_before_private_upgrade"] = report.pop("source_snapshot_sha256")
        report["prepared_snapshot_sha256"] = file_digest(args.output/"source-snapshot.db")
        report["inputs_verified_again"] = True
        report["storage_environment"] = "isolated_Docker_named_volume_not_the_source_Windows_bind_mount"
        write_report(report_path,report)
        print(json.dumps({"preflight_passed":True,"pages":len(copied),"assets":len(private["assets"]),
                          "paid_requests_started":False}),flush=True)
    finally:
        engine.dispose()


def smoke(args):
    import numpy as np
    from app.pipeline.staged import run_stage
    from app.services.execution import ExecutionPolicy, execution_runtime
    from app.services.workflow_state import apply_delta, record
    from app.storage import get_storage
    from PIL import Image

    if (args.output/"dispatch-started.json").exists():
        raise ValueError("Do not interfere with an already submitted benchmark")
    private = json.loads((args.output/"private-inputs.json").read_text(encoding="utf-8"))
    storage = get_storage()
    engine = create_database_engine(get_settings().resolved_database_url)
    try:
        with Session(engine) as db:
            pages = [db.get(ImagePage,identifier) for identifier in private["page_ids"]]
            page = max(pages,key=lambda page:len(page.regions))
            initial = capture_page(db,page.id)
            models = [record(row) for row in db.scalars(select(ModelConfig).where(ModelConfig.enabled.is_(True)))]
    finally:
        engine.dispose()
    settings = get_settings()
    execution_runtime().update(ExecutionPolicy(mode="fixed",max_pages=1,cpu_workers=2),persist=False)
    policy = {"request":{"start_stage":"mask","end_stage":"rendering","force":True,
        "options":{"repair_geometry":"source","rebuild_clean":True}},"models":models,
        "settings":{key:getattr(settings,key) for key in ("segmentation_backend","text_segmentation_enabled","repair_auto_retry","ocr_review_threshold")}}
    directory = storage.page_dir(initial["page"]["project_id"],initial["page"]["id"])/"workflows"/uuid4().hex
    state = initial
    started = time.monotonic()
    for stage in ("mask","inpainting","rendering"):
        result = run_stage(stage,state,policy,directory/uuid4().hex,storage,lambda:None)
        state = apply_delta(state,result["changes"])
    with Image.open(storage.absolute(initial["page"]["original_path"])) as source:
        source = np.asarray(source.convert("RGB"))
    with Image.open(storage.absolute(state["page"]["clean_path"])) as clean:
        clean = np.asarray(clean.convert("RGB"))
    allowed = np.zeros(source.shape[:2],bool)
    for region in state["regions"].values():
        if region["pixel_mask_path"]:
            with Image.open(storage.absolute(region["pixel_mask_path"])) as mask:
                allowed |= np.asarray(mask.convert("L"))>0
    inference = execution_runtime().snapshot()["inference"]
    report = {"scope":"real_mask_LaMa_render_no_OCR_no_translation","page_id":initial["page"]["id"],
        "regions":len(initial["regions"]),"seconds":time.monotonic()-started,
        "outside_mask_identical":bool(np.array_equal(source[~allowed],clean[~allowed])),
        "inference":inference,"paid_requests_started":False}
    report["pass"] = report["outside_mask_identical"] and any(row["name"]=="comic-text-detector"
        and row["backend"]=="onnx_cuda" and row["device"]=="cuda:0" for row in inference) and any(
        row["name"]=="lama" and str(row["device"]).startswith("cuda") for row in inference)
    write_report(args.output/"smoke-report.json",report)
    print(json.dumps(report),flush=True)
    if not report["pass"]:
        raise ValueError("Native smoke gate failed; do not dispatch the paid acceptance batch")


def serve(args):
    import uvicorn

    if not (args.output/"preparation-report.json").is_file():
        raise ValueError("The isolated dataset must be prepared before starting its API")
    write_report(args.output/"server.json",{"pid":os.getpid()})
    uvicorn.run("app.main:app",host="127.0.0.1",port=8000,log_level="warning",access_log=False)


def dispatch(args):
    if not args.allow_paid_requests:
        raise ValueError("The real run requires explicit --allow-paid-requests authorization")
    if not json.loads((args.output/"preparation-report.json").read_text()).get("inputs_verified_again"):
        raise ValueError("Preflight must pass before a paid run")
    if not json.loads((args.output/"smoke-report.json").read_text()).get("pass"):
        raise ValueError("Native mask/repair/render smoke must pass before a paid run")
    private = json.loads((args.output/"private-inputs.json").read_text(encoding="utf-8"))
    if args.project!=private["project_id"]:
        raise ValueError("Prepared project differs from the requested project")
    identifier = uuid4().hex
    marker = {"run_id":identifier,"started_at":datetime.now(timezone.utc).isoformat()}
    # Persist intent BEFORE sending. An unknown POST outcome is recovered by
    # its tag, never by automatically submitting a second paid batch.
    write_report(args.output/"dispatch-started.json",marker,exclusive=True)
    with httpx.Client(base_url="http://127.0.0.1:8000",timeout=180) as client:
        response = client.post(f"/api/projects/{args.project}/batch-process",json={
            "start_stage":"translation","end_stage":"rendering","force":True,
            "image_ids":private["page_ids"],"only_with_regions":True,
            "options":{"repair_geometry":"source","rebuild_clean":True,"acceptance_run_id":identifier},
        })
        response.raise_for_status()
        tasks = response.json()
    if len(tasks)!=337 or {task["image_id"] for task in tasks}!=set(private["page_ids"]):
        raise ValueError("Dispatch differs from the prepared 337-page set; do not resubmit")
    state = {**marker,"task_ids":[task["id"] for task in tasks],"batch_ids":sorted({task["batch_id"] for task in tasks})}
    write_report(args.output/"dispatch.json",state)
    print(json.dumps({"submitted":len(tasks),"batch_ids":state["batch_ids"]}),flush=True)
    observe(args)


def recover_dispatch(args):
    saved = args.output/"dispatch.json"
    if saved.exists():
        return json.loads(saved.read_text(encoding="utf-8"))
    marker = json.loads((args.output/"dispatch-started.json").read_text(encoding="utf-8"))
    engine = create_database_engine(get_settings().resolved_database_url)
    try:
        with Session(engine) as db:
            tasks = list(db.scalars(select(ProcessingTask).where(ProcessingTask.project_id==args.project)))
            tasks = [task for task in tasks if task.payload.get("options",{}).get("acceptance_run_id")==marker["run_id"]]
            if len(tasks)!=337:
                raise ValueError("Dispatch outcome is not confirmed; inspect it, never automatically resend")
            state = {**marker,"task_ids":[task.id for task in tasks],"batch_ids":sorted({task.batch_id for task in tasks})}
        write_report(saved,state)
        return state
    finally:
        engine.dispose()


def observe(args):
    state = recover_dispatch(args)
    started = datetime.fromisoformat(state["started_at"])
    server = psutil.Process(json.loads((args.output/"server.json").read_text())["pid"])
    processes = {}
    peaks = {"rss_sum_bytes":0,"cpu_aggregate_percent":0.,"gpu_used_bytes":0,"gpu_utilization_percent":0.,
             "page_concurrency":0,"cpu_concurrency":0,"model_concurrency":0}
    samples = []
    previous = args.output/"progress.json"
    if previous.exists():
        old = json.loads(previous.read_text(encoding="utf-8"))
        peaks.update(old.get("peaks",{}))
        samples = old.get("samples",[])
    with httpx.Client(base_url="http://127.0.0.1:8000",timeout=30) as client:
        while True:
            response = client.post("/api/tasks/status",json=state["task_ids"])
            response.raise_for_status()
            tasks = response.json()
            response = client.get("/api/execution")
            response.raise_for_status()
            runtime = response.json()
            rss,cpu = 0,0.
            for process in [server,*server.children(recursive=True)]:
                try:
                    tracked = processes.setdefault(process.pid,process)
                    rss += tracked.memory_info().rss
                    cpu += tracked.cpu_percent()
                except psutil.Error:
                    pass
            peaks["rss_sum_bytes"] = max(peaks["rss_sum_bytes"],rss)
            peaks["cpu_aggregate_percent"] = max(peaks["cpu_aggregate_percent"],cpu)
            for device in runtime.get("gpu",{}).get("devices",[]):
                peaks["gpu_used_bytes"] = max(peaks["gpu_used_bytes"],device["used_bytes"])
                peaks["gpu_utilization_percent"] = max(peaks["gpu_utilization_percent"],device["utilization_percent"] or 0)
            for output,key in (("page_concurrency","pages"),("cpu_concurrency","cpu"),("model_concurrency","models")):
                peaks[output] = max(peaks[output],runtime[key].get("peak",runtime[key]["active"]))
            counts = dict(Counter(task["status"] for task in tasks))
            now = datetime.now(timezone.utc)
            sample = {"at":now.isoformat(),"elapsed_seconds":(now-started).total_seconds(),"statuses":counts,
                      "active_stages":dict(Counter(stage for task in tasks for stage in task.get("active_stages",[]))),
                      "waiting":dict(Counter(task["waiting_reason"] for task in tasks if task.get("waiting_reason")))}
            samples.append(sample)
            report = {"scope":"one_real_paid_337_page_translation_and_repair_run","task_count":len(tasks),
                "batch_ids":state["batch_ids"],"statuses":counts,"peaks":peaks,"samples":samples,
                "elapsed_seconds":sample["elapsed_seconds"],"execution_status":runtime,
                "terminal":all(task["status"] in {"COMPLETED","FAILED","CANCELLED"} for task in tasks),
                "errors":[{"id":task["id"],"image_id":task["image_id"],"kind":task.get("error_kind"),
                           "message":task.get("error_message")} for task in tasks if task["status"] in {"FAILED","CANCELLED"}],
                "no_automatic_unknown_outcome_retries":True}
            report["resource_sampling"] = {"interval_seconds":5,"gpu_cache_seconds":10,
                "gpu_scope":"whole_device_including_other_processes","rss_scope":"sum_of_backend_and_child_RSS"}
            report["storage_environment"] = "isolated_Docker_named_volume_not_the_source_Windows_bind_mount"
            if report["terminal"] and all(task.get("finished_at") for task in tasks):
                finished = max(datetime.fromisoformat(task["finished_at"]) for task in tasks)
                report["wall_seconds_from_dispatch"] = (finished-started).total_seconds()
            write_report(args.output/"progress.json",report)
            print(json.dumps(sample),flush=True)
            if report["terminal"] or counts.get("PAUSED"):
                write_report(args.output/"execution-report.json",report)
                return
            time.sleep(5)


def audit(args):
    """Verify source immutability and all terminal pages without paid calls."""
    import numpy as np
    from app.services.layout.font_sizing import automatic_font_size
    from app.services.workflow_state import page_version
    from PIL import Image

    private = json.loads((args.output/"private-inputs.json").read_text(encoding="utf-8"))
    dispatch = recover_dispatch(args)
    source_copy = args.output/f"source-audit-{uuid4().hex}.db"
    backup_sqlite(args.source/"mangaflow.db",source_copy)
    source_engine = create_database_engine(f"sqlite:///{source_copy}")
    engine = create_database_engine(get_settings().resolved_database_url)
    try:
        upgrade_database(source_engine)
        source_states,_ = frozen_inputs(source_engine,args.project)
        actual_fingerprint = fingerprint(source_states)
        source_same = actual_fingerprint==private["source_fingerprint"]
        assets_same = all(file_digest(args.source/path)==value["sha256"] for path,value in private["assets"].items())
        root = get_settings().data_dir
        cases,timings,errors = [],{},[]
        with Session(engine) as db:
            tasks = list(db.scalars(select(ProcessingTask).where(ProcessingTask.id.in_(dispatch["task_ids"]))))
            if len(tasks)!=337 or any(task.status not in {"COMPLETED","FAILED","CANCELLED"} for task in tasks):
                raise ValueError("All 337 tasks must be terminal before final acceptance audit")
            task_ids = [task.id for task in tasks]
            for run in db.scalars(select(TaskExecutionRun).where(TaskExecutionRun.task_id.in_(task_ids))):
                for key,value in run.timings.items():
                    total = timings.setdefault(key,{"seconds":0.,"calls":0})
                    total["seconds"] += value["seconds"]
                    total["calls"] += value["calls"]
            runs = db.scalar(select(func.count()).select_from(TaskExecutionRun).where(TaskExecutionRun.task_id.in_(task_ids)))
            for task in tasks:
                workflow = db.scalar(select(TaskWorkflow).where(TaskWorkflow.task_id==task.id)
                                     .order_by(TaskWorkflow.created_at.desc()).limit(1))
                if workflow is None:
                    cases.append({"task_id":task.id,"error":"missing_workflow"})
                    continue
                initial = workflow.input_state
                final = capture_page(db,task.image_id)
                if task.status!="COMPLETED":
                    errors.append({"id":task.id,"kind":task.error_kind,"message":task.error_message})
                    cases.append({"task_id":task.id,"completed":False,
                        "unpublished_page_unchanged":page_version(initial)==page_version(final)})
                    continue
                if set(initial["regions"])!=set(final["regions"]):
                    cases.append({"task_id":task.id,"error":"regions_changed"})
                    continue
                with Image.open(root/initial["page"]["original_path"]) as image:
                    source = np.asarray(image.convert("RGB"))
                with Image.open(root/final["page"]["clean_path"]) as image:
                    clean = np.asarray(image.convert("RGB"))
                allowed = np.zeros(source.shape[:2],bool)
                manual,font,rotation,geometry = True,True,True,True
                manual_count,font_count,rotation_count = 0,0,0
                for identifier,row in final["regions"].items():
                    old = initial["regions"][identifier]
                    if row["pixel_mask_path"]:
                        with Image.open(root/row["pixel_mask_path"]) as mask:
                            allowed |= np.asarray(mask.convert("L"))>0
                    if old["layout_data"].get("mask_generation",{}).get("method")=="manual_mask" and old["pixel_mask_path"]:
                        manual_count += 1
                        manual &= bool(row["pixel_mask_path"]) and file_digest(root/old["pixel_mask_path"])==file_digest(root/row["pixel_mask_path"])
                    if not automatic_font_size(old["layout_data"],old["font_size"]):
                        font_count += 1
                        font &= abs(old["font_size"]-row["font_size"])<.001
                    if old["layout_data"].get("text_rotation",{}).get("source")=="user" or abs(old["rotation"])>.01:
                        rotation_count += 1
                        rotation &= abs(old["rotation"]-row["rotation"])<.001
                    geometry &= old["bbox"]==row["bbox"] and old["polygon"]==row["polygon"]
                with Image.open(root/final["page"]["rendered_path"]) as image:
                    rendered_size = image.size
                cases.append({"task_id":task.id,"completed":True,"published":workflow.status=="PUBLISHED",
                    "outside_mask_identical":bool(source.shape==clean.shape and np.array_equal(source[~allowed],clean[~allowed])),
                    "rendered_size_valid":rendered_size==(initial["page"]["width"],initial["page"]["height"]),
                    "manual_masks_unchanged":bool(manual),"manual_mask_count":manual_count,
                    "manual_fonts_unchanged":bool(font),"manual_font_count":font_count,
                    "intentional_rotations_unchanged":bool(rotation),"intentional_rotation_count":rotation_count,
                    "source_geometry_unchanged":bool(geometry)})
        conflicts = [error for error in errors if any(word in str(error).lower() for word in
            ("database is locked","deadlock","serializationfailure","lock timeout","sqlstate=40001","sqlstate=40p01"))]
        report = {"scope":"all_337_terminal_pages_and_source_immutability_no_paid_requests",
            "source_fingerprint_before":private["source_fingerprint"],"source_fingerprint_after":actual_fingerprint,
            "source_database_unchanged":source_same,"source_assets_unchanged":assets_same,
            "task_count":len(tasks),"timing_runs":runs,"database_conflict_failures":len(conflicts),
            "inclusive_component_timings_do_not_sum_to_wall_clock":timings,"cases":cases,"errors":errors}
        report["quality_pass"] = source_same and assets_same and all(not row.get("error") and (
            all(row[key] for key in ("published","outside_mask_identical","rendered_size_valid","manual_masks_unchanged",
                "manual_fonts_unchanged","intentional_rotations_unchanged","source_geometry_unchanged")) if row.get("completed")
            else row.get("unpublished_page_unchanged",False)) for row in cases)
        write_report(args.output/"quality-report.json",report)
        print(json.dumps({key:value for key,value in report.items() if key not in {"cases","inclusive_component_timings_do_not_sum_to_wall_clock"}}),flush=True)
    finally:
        source_engine.dispose()
        engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--project",required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--mode",choices=["prepare","preflight","smoke","serve","run","observe","audit"],required=True)
    parser.add_argument("--allow-paid-requests",action="store_true")
    args = parser.parse_args()
    {"prepare":prepare,"preflight":preflight,"smoke":smoke,"serve":serve,"run":dispatch,"observe":observe,"audit":audit}[args.mode](args)


if __name__=="__main__":
    main()

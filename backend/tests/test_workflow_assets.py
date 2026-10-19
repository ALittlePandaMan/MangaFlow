from __future__ import annotations

import os
import time
from uuid import uuid4

import pytest
from app.core.migrations import upgrade_database
from app.models import (
    EditorHistoryEntry,
    ImagePage,
    ProcessingTask,
    Project,
    RegionRevision,
    StageCheckpoint,
    TaskWorkflow,
    TextRegion,
)
from app.services.workflow_assets import collect_project_workflow_assets
from app.storage import StorageManager
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker
from test_database_migration import target as target  # noqa: F401


@pytest.fixture
def scene(target,tmp_path):
    upgrade_database(target)
    sessions = sessionmaker(bind=target,expire_on_commit=False)
    storage = StorageManager(tmp_path)
    with sessions() as db:
        project = Project(name="workflow collection")
        db.add(project)
        db.flush()
        page = ImagePage(project_id=project.id,filename="original.png",original_path="unused.png",width=10,height=10)
        db.add(page)
        db.flush()
        region = TextRegion(image_id=page.id,region_key="R001")
        db.add(region)
        db.commit()
        identifiers = project.id,page.id,region.id
    return sessions,storage,*identifiers


def generated(scene,name,*,old=True):
    _,storage,project,page,_ = scene
    path = storage.page_dir(project,page)/"workflows"/"workflow"/"checkpoint"/"masks"/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(b"image")
    if old:
        os.utime(path,(time.time()-8*24*3600,)*2)
    return path


def collect(scene,**kwargs):
    sessions,storage,project,*_ = scene
    return collect_project_workflow_assets(project,sessions=sessions,storage=storage,time_budget=5,**kwargs)


def test_retains_page_region_revision_and_both_history_directions(scene):
    sessions,storage,project,page,region = scene
    paths = {name:generated(scene,name+".png") for name in ("page","region","revision","undo","redo","orphan")}
    young = generated(scene,"young.png",old=False)
    with sessions() as db:
        db.get(ImagePage,page).clean_path = storage.relative(paths["page"])
        db.get(TextRegion,region).pixel_mask_path = storage.relative(paths["region"])
        db.add(RegionRevision(region_id=region,action="old",snapshot={"pixel_mask_path":storage.relative(paths["revision"])}))
        db.add(EditorHistoryEntry(project_id=project,image_id=page,action="undo",before={"mask":storage.relative(paths["undo"])},after={},applied=True))
        db.add(EditorHistoryEntry(project_id=project,image_id=page,action="redo",before={},after={"mask":storage.relative(paths["redo"])},applied=False))
        db.commit()
    assert collect(scene) == {"removed_files":1,"removed_bytes":5,"deferred":False}
    assert not paths["orphan"].exists() and young.exists()
    assert all(path.exists() for name,path in paths.items() if name != "orphan")


def test_collects_only_old_unreferenced_uuid_export_images_and_keeps_published_output(scene):
    sessions,storage,_,page,_ = scene
    paths = []
    for name in ("export-"+"1"*32+".png", "export-"+"2"*32+".png", "export-manual.png", "translated.png"):
        path = storage.page_dir(scene[2],page)/"rendered"/name
        path.write_bytes(b"image")
        os.utime(path,(time.time()-8*24*3600,)*2)
        paths.append(path)
    with sessions() as db:
        db.get(ImagePage,page).rendered_path = storage.relative(paths[0])
        db.commit()
    assert collect(scene)["removed_files"] == 1
    assert paths[0].exists() and not paths[1].exists()
    assert paths[2].exists() and paths[3].exists()


def test_retains_unpublished_success_and_unknown_cloud_attempts(scene):
    sessions,storage,project,page,_ = scene
    saved = generated(scene,"saved.png")
    forensic = generated(scene,"forensic.png")
    partial = generated(scene,"failed-partial.png")
    with sessions() as db:
        task = ProcessingTask(project_id=project,image_id=page,task_type="full",status="FAILED")
        db.add(task)
        db.flush()
        workflow = TaskWorkflow(task_id=task.id,generation=1,input_fingerprint="input",request_fingerprint="request",input_state={},policy={})
        db.add(workflow)
        db.flush()
        db.add(StageCheckpoint(workflow_id=workflow.id,stage="mask",attempt=1,generation=1,input_fingerprint="mask",status="SUCCEEDED",
            artifacts={storage.relative(saved):{"sha256":"unused"}}))
        # Unknown attempts have no committed manifest; preserve their entire
        # directory as evidence rather than inferring that the request failed.
        unknown = StageCheckpoint(workflow_id=workflow.id,stage="translation",attempt=1,generation=1,input_fingerprint="translation",status="UNKNOWN",cloud_request=True)
        db.add(unknown)
        db.flush()
        destination = storage.page_dir(project,page)/"workflows"/workflow.id/unknown.id/"versions"/"response.png"
        destination.parent.mkdir(parents=True)
        forensic.replace(destination)
        db.commit()
    assert collect(scene)["removed_files"] == 1
    assert saved.exists() and destination.exists() and not partial.exists()


@pytest.mark.parametrize("busy",["task","history"])
def test_busy_project_is_deferred_and_does_not_change_timestamps(scene,busy):
    sessions,_,project,page,_ = scene
    path = generated(scene,"orphan.png")
    with sessions() as db:
        original = db.get(Project,project).updated_at
        if busy == "task":
            db.add(ProcessingTask(project_id=project,image_id=page,task_type="full",status="PAUSED"))
        else:
            db.add(EditorHistoryEntry(project_id=project,image_id=page,action="pending",before={},after={},pending=True))
        db.commit()
    assert collect(scene)["deferred"] and path.exists()
    with sessions() as db:
        assert db.get(Project,project).updated_at == original


def test_sweep_is_bounded_and_scanning_eventually_reaches_later_pages(scene):
    sessions,storage,project,page,_ = scene
    first = generated(scene,"a-referenced.png")
    with sessions() as db:
        db.get(ImagePage,page).clean_path = storage.relative(first)
        db.commit()
    others = [generated(scene,f"orphan-{i}.png") for i in range(4)]
    for _ in range(10):
        assert collect(scene,scan_limit=2,max_files=1)["removed_files"] <= 1
    assert first.exists() and not any(path.exists() for path in others)


def test_external_symlink_is_never_followed_and_sqlite_timeout_is_restored(scene,tmp_path):
    sessions,storage,project,page,_ = scene
    generated(scene,"orphan.png")
    outside = tmp_path.parent / f"outside-{uuid4().hex}.png"
    outside.write_bytes(b"do not delete")
    directory = storage.page_dir(project,page)/"workflows"/"workflow"/"checkpoint"/"masks"
    link = directory/"outside-link.png"
    link.symlink_to(outside)
    try:
        collect(scene)
        assert outside.read_bytes() == b"do not delete" and link.is_symlink()
        with sessions() as db:
            if db.bind.dialect.name == "sqlite":
                assert db.connection().exec_driver_sql("PRAGMA busy_timeout").scalar() == 30000
            assert db.scalar(select(Project.id).where(Project.id == project)) == project
    finally:
        outside.unlink()


def test_page_edit_lock_defers_collection_immediately_instead_of_waiting(scene):
    sessions,_,_,page,_ = scene
    path = generated(scene,"orphan.png")
    with sessions() as writer:
        if writer.bind.dialect.name == "sqlite":
            writer.execute(update(ImagePage).where(ImagePage.id == page).values(updated_at=ImagePage.updated_at))
        else:
            writer.execute(select(ImagePage.id).where(ImagePage.id == page).with_for_update())
        started = time.monotonic()
        assert collect(scene)["deferred"] and path.exists()
        assert time.monotonic()-started < 2
        writer.rollback()
    assert collect(scene)["removed_files"] == 1

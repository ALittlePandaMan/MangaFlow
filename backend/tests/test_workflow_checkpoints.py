from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from app.core.migrations import upgrade_database
from app.models import ImagePage, ProcessingTask, Project, StageCheckpoint, TextRegion
from app.services.checkpoints import CheckpointStore, StageBusy, StaleExecution, UnknownCloudOutcome
from app.services.workflow_state import (
    BranchConflict,
    apply_delta,
    capture_page,
    delta,
    fingerprint,
    merge_branches,
    page_version,
    private_page,
)
from app.storage import StorageManager
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def workflow(target, tmp_path):
    upgrade_database(target)
    sessions = sessionmaker(bind=target, expire_on_commit=False, autoflush=False)
    with sessions() as db:
        project = Project(name="isolated workflow")
        db.add(project)
        db.flush()
        page = ImagePage(project_id=project.id, filename="original.png", original_path="missing.png", width=20, height=30)
        db.add(page)
        db.flush()
        db.add(TextRegion(image_id=page.id, region_key="R001", source_text="原文", bbox=[1, 2, 3, 4]))
        task = ProcessingTask(project_id=project.id, image_id=page.id, task_type="full", status="RUNNING", execution_generation=1)
        db.add(task)
        db.commit()
        state = capture_page(db, page.id)
        task_id = task.id
    store = CheckpointStore(sessions, StorageManager(tmp_path))
    lease = store.begin(task_id, 1, state, {"provider": "frozen"})
    return sessions, store, lease, state


def test_success_reuses_same_workflow_on_retry_but_fences_old_worker(workflow):
    sessions, store, lease, state = workflow
    ticket = store.claim(lease, "translation", fingerprint(state), cloud=True)
    store.succeed(ticket, {"translation": "saved response"})
    late = store.claim(lease, "mask", fingerprint(state))
    with sessions() as db:
        db.get(ProcessingTask, lease.task_id).execution_generation = 2
        db.commit()
    newer = store.begin(lease.task_id, 2, state, {"provider": "frozen"})
    assert newer.id == lease.id
    assert store.successful(newer, "translation", fingerprint(state)) == {"translation": "saved response"}
    with pytest.raises(StaleExecution):
        store.succeed(late, {"mask": "must not be published"})
    replacement = store.claim(newer, "mask", fingerprint(state))
    assert replacement.attempt == 2 and store.directory(replacement) != store.directory(late)


def test_changed_inputs_or_policy_do_not_reuse_stage_results(workflow):
    _, store, lease, state = workflow
    altered = apply_delta(state, {"regions": {"put": {next(iter(state["regions"])): {"source_text": "新原文"}}}})
    assert store.begin(lease.task_id, 1, altered, {"provider": "frozen"}).id != lease.id
    assert store.begin(lease.task_id, 1, state, {"provider": "changed"}).id != lease.id


def test_unfinished_cloud_request_is_unknown_not_automatically_repeated(workflow):
    sessions, store, lease, state = workflow
    ticket = store.claim(lease, "translation", fingerprint(state), cloud=True)
    with sessions() as db:
        db.get(ProcessingTask, lease.task_id).execution_generation = 2
        db.commit()
    retry = store.begin(lease.task_id, 2, state, {"provider": "frozen"})
    with pytest.raises(UnknownCloudOutcome):
        store.claim(retry, "translation", fingerprint(state), cloud=True)
    with sessions() as db:
        checkpoint = db.get(StageCheckpoint, ticket.id)
        assert checkpoint.status == "UNKNOWN" and not checkpoint.result
        checkpoint.status = "ACKNOWLEDGED"
        db.commit()
    assert store.claim(retry, "translation", fingerprint(state), cloud=True).attempt == 2


def test_cancelled_attempt_cannot_publish_a_completed_native_call(workflow):
    sessions, store, lease, state = workflow
    ticket = store.claim(lease, "mask", fingerprint(state))
    with sessions() as db:
        db.get(ProcessingTask, lease.task_id).status = "CANCELLING"
        db.commit()
    with pytest.raises(StaleExecution):
        store.succeed(ticket, {"unexpected": "late result"})
    with sessions() as db:
        assert not db.get(StageCheckpoint, ticket.id).result


def test_changed_artifact_invalidates_checkpoint_without_reusing_its_directory(workflow):
    sessions, store, lease, state = workflow
    ticket = store.claim(lease, "mask", fingerprint(state))
    directory = store.directory(ticket)
    directory.mkdir(parents=True)
    output = directory / "mask.png"
    output.write_bytes(b"immutable output")
    store.succeed(ticket, {"saved": True})
    assert store.successful(lease, "mask", fingerprint(state)) == {"saved": True}
    output.write_bytes(b"changed")
    assert store.successful(lease, "mask", fingerprint(state)) is None
    with sessions() as db:
        assert db.get(StageCheckpoint, ticket.id).status == "INVALID"
    assert store.directory(store.claim(lease, "mask", fingerprint(state))) != directory


def test_concurrent_stage_claim_has_only_one_owner(workflow):
    sessions, store, lease, state = workflow

    def claim():
        try:
            return store.claim(lease, "mask", fingerprint(state))
        except StageBusy:
            return None

    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: claim(), range(4)))
    assert len([ticket for ticket in results if ticket]) == 1
    with sessions() as db:
        assert len(list(db.scalars(select(StageCheckpoint)))) == 1


@pytest.mark.parametrize("read", ["successful", "validate_proofs"])
def test_checkpoint_read_does_not_lock_task_while_materializing_result(workflow, monkeypatch, read):
    sessions, store, lease, state = workflow
    key = fingerprint(state)
    ticket = store.claim(lease, "mask", key)
    store.succeed(ticket, {"saved": True})
    original = store._workflow
    checked = []

    def validate(db, lease, **kwargs):
        result = original(db, lease, **kwargs)
        with sessions() as other:
            other.execute(select(ProcessingTask).where(ProcessingTask.id == lease.task_id).with_for_update(nowait=True))
        checked.append(True)
        return result

    monkeypatch.setattr(store, "_workflow", validate)
    if read == "successful":
        assert store.successful(lease, "mask", key) == {"saved": True}
    else:
        assert store.validate_proofs(lease, {"mask": key}) == [ticket.id]
    assert checked == [True]


def test_private_compute_cannot_modify_published_page_and_its_delta_replays(workflow):
    sessions, _, _, state = workflow
    identifier = next(iter(state["regions"]))
    with private_page(state) as (db, page):
        page.regions[0].translated_text = "only in the worker"
        db.commit()
        result = capture_page(db, page.id, compact_history=False)
    changes = delta(state, result)
    assert "source_text" not in changes["regions"]["put"][identifier]
    assert apply_delta(state, changes)["regions"][identifier]["translated_text"] == "only in the worker"
    with sessions() as db:
        assert not db.get(TextRegion, identifier).translated_text
        assert page_version(capture_page(db, state["page"]["id"])) == page_version(state)


def test_branch_merge_keeps_disjoint_layout_keys_and_rejects_conflicts():
    base = {"layout_data": {"manual": True, "mask_generation": {"method": "manual_mask"}}, "text": "old"}
    translation = {**base, "layout_data": {**base["layout_data"], "translation_fallback": False}, "text": "new"}
    repair = {**base, "layout_data": {**base["layout_data"], "repair_quality": {"status": "ok"}}}
    joined = merge_branches(base, translation, repair)
    assert joined["text"] == "new"
    assert joined["layout_data"] == {**translation["layout_data"], "repair_quality": {"status": "ok"}}
    with pytest.raises(BranchConflict, match="state/text"):
        merge_branches(base, translation, {**repair, "text": "different"})

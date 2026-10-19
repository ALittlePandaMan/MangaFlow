"""Prepare lightweight region edits outside the application's write locks."""
from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

from app.core.transactions import has_pending_writes, retry_write
from app.models import EditorHistoryEntry, ImagePage, Project, RegionRevision, TextRegion
from app.services.editor_history import PAGE_FIELDS, _archive_paths, _finish_entry, ensure_page_idle
from app.services.regions import SNAPSHOT_FIELDS
from app.services.workflow_state import capture_page, decode, delta, page_version, private_page, record
from app.storage import get_storage
from fastapi import HTTPException
from sqlalchemy import select, update


def _capture(db, page_id, identifiers):
    page = db.get(ImagePage,page_id,populate_existing=True)
    if page is None:
        raise HTTPException(404,"Image page not found")
    ensure_page_idle(db,page)
    query = select(TextRegion).where(TextRegion.image_id==page_id)
    if identifiers is not None:
        query = query.where(TextRegion.id.in_(identifiers))
    rows = list(db.scalars(query
                           .execution_options(populate_existing=True)))
    if identifiers is not None and {row.id for row in rows}!=set(identifiers):
        raise HTTPException(409,"区域列表已变化，请刷新后重试")
    return {"project":record(db.get(Project,page.project_id,populate_existing=True)),"page":record(page),
            "regions":{row.id:record(row) for row in rows},"translations":{},"revisions":{}}


def _archive(state, identifiers):
    directory = get_storage().project_dir(state["page"]["project_id"])
    rows = sorted(state["regions"].values(),key=lambda row:(row["reading_order"],row["created_at"],row["id"]))
    history = {"page":{field:deepcopy(state["page"][field]) for field in PAGE_FIELDS},
        "regions":[{field:deepcopy(row[field]) for field in ("id","region_key",*SNAPSHOT_FIELDS)} for row in rows]}
    if identifiers is not None:
        history["_partial_region_ids"] = sorted(identifiers)
    return _archive_paths(history,directory,directory/"history"/"assets")


def apply_region_edits(db, page_id: str, changes, action: str, apply_patch):
    """Existing region IDs only; files/history are prepared before short save.

    No real Session crosses a worker boundary. A tiny disposable adapter keeps
    existing typography/geometry rules unchanged; the actual database receives
    only a validated field delta and its revisions in one retryable transaction.
    """
    identifiers = [identifier for identifier,_ in changes]
    if not identifiers or len(set(identifiers))!=len(identifiers):
        raise HTTPException(422,"需要非重复的区域列表")
    def operation(private,page):
        for identifier,payload in changes:
            apply_patch(private,private.get(TextRegion,identifier),payload)
        return {"region_ids":identifiers}

    apply_page_edit(db,page_id,operation,action,scope=identifiers)
    return [db.get(TextRegion,identifier,populate_existing=True) for identifier in identifiers]


def apply_page_edit(db, page_id: str, operation, action: str, *, scope=None, prepare_extra=None):
    """Prepare files and a plain field delta, then publish one undoable edit.

    Structural changes require a whole-page snapshot. A scoped edit can only
    modify retained IDs. The operation runs in a disposable local adapter, not
    in an application database transaction, and is never replayed on DB retry.
    """
    if has_pending_writes(db):
        raise ValueError("Region edits require committed input")
    initial = _capture(db,page_id,scope)
    project_id = initial["page"]["project_id"]
    version = page_version(initial)
    db.rollback()  # No connection/row/write lock spans the archives or adapter.
    before = _archive(initial,scope)
    with private_page(initial) as (private,page):
        metadata = operation(private,page)
        private.commit()
        final = capture_page(private,page_id,compact_history=False)
        directory = get_storage().project_dir(project_id)
        for revision in final["revisions"].values():
            # A subsequent delete/merge can remove live masks. Region-level
            # revision restoration must remain valid independently of undo.
            revision["snapshot"] = _archive_paths(revision["snapshot"],directory,directory/"history"/"assets")
        after = _archive(final,scope)
        extra = prepare_extra(private,page,before,after,metadata) if prepare_extra else {}
    identifiers = metadata.get("region_ids",[])
    values = delta(initial,final)
    if values["translations"]["put"] or values["translations"]["delete"] or values["revisions"]["delete"]:
        raise ValueError("An editor operation cannot replace translation or revision history")
    if scope is not None and (values["regions"]["delete"] or set(values["regions"]["put"])-set(scope)):
        raise ValueError("A scoped edit cannot create or delete regions")
    if set(values["page"])-{"clean_path","rendered_path","text_layer_path","metadata_json","status","current_stage","error_message"}:
        raise ValueError("An editor operation cannot replace the page's identity or source image")

    def save():
        # Same lock order as checkpoint publication: project -> page -> journal.
        db.execute(update(Project).where(Project.id==project_id).values(updated_at=Project.updated_at))
        page = db.scalar(select(ImagePage).where(ImagePage.id==page_id).with_for_update())
        if page is None:
            raise HTTPException(404,"Image page not found")
        actual = _capture(db,page_id,scope)
        if page_version(actual)!=version:
            raise HTTPException(409,"文本框在保存期间发生变化，请刷新后重试；未覆盖其他编辑")
        for key,value in values["page"].items():
            setattr(page,key,deepcopy(value))
        for identifier in values["regions"]["delete"]:
            db.delete(db.get(TextRegion,identifier))
        # Renumbering/merging may exchange names. Release old unique keys as
        # one DB-only step before applying the prepared final names.
        for identifier,patch in values["regions"]["put"].items():
            if identifier in initial["regions"] and "region_key" in patch:
                db.get(TextRegion,identifier).region_key = f"__edit_{uuid4().hex[:16]}"
        db.flush()
        for identifier,patch in values["regions"]["put"].items():
            region = db.get(TextRegion,identifier)
            if identifier not in initial["regions"]:
                if region is not None:
                    raise HTTPException(409,"新区域 ID 已被使用，请刷新后重试")
                db.add(TextRegion(**decode(TextRegion,patch)))
            else:
                for key,value in decode(TextRegion,patch).items():
                    setattr(region,key,value)
        db.flush()
        db.add_all(RegionRevision(**decode(RegionRevision,row)) for row in values["revisions"]["put"].values())
        if before!=after:
            entry = EditorHistoryEntry(project_id=project_id,image_id=page_id,action=action,
                before=before,after=after,region_ids=identifiers)
            db.add(entry)
            db.flush()
            _finish_entry(db,entry,after)
            if extra:
                entry.after = {**entry.after,**deepcopy(extra)}
            return entry.id
        return None
    entry_id = retry_write(db,save)
    # SQLite reloads naive UTC whereas PostgreSQL reloads aware UTC. Return
    # the database representation consistently so mask optimistic-version
    # tokens match the subsequent mask-history response byte-for-byte.
    db.expire_all()
    return metadata,entry_id

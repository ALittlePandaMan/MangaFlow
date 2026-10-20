"""Value-only page snapshots and conflict-detecting branch merges.

No session, ORM identity, provider instance, or mutable image crosses a worker
boundary. Stages use connection-private PostgreSQL temporary tables, with no
business schema on their search path. Closing the connection removes all rows.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime

from app.core.config import get_settings
from app.core.database import Base
from app.core.db_engine import create_database_engine
from app.core.serialization import normalized
from app.models import ImagePage, Project, RegionRevision, TextRegion, Translation
from sqlalchemy import DateTime, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateTable

COLLECTIONS = {"regions": TextRegion, "translations": Translation, "revisions": RegionRevision}
GEOMETRY_HISTORY = {"bbox", "polygon", "translated_bbox", "translated_polygon", "rotation", "perspective_warp"}
VOLATILE_PAGE_FIELDS = {"status", "current_stage", "error_message", "updated_at"}
_MISSING = object()
PIPELINE_STAGES = ("detection", "ocr", "translation", "mask", "inpainting", "rendering")


def requested_stages(request: dict) -> tuple[str, ...]:
    start = request.get("start_stage", "detection")
    end = request.get("end_stage", "rendering")
    start = "detection" if start == "full" else start
    end = "rendering" if end == "full" else end
    if start not in PIPELINE_STAGES or end not in PIPELINE_STAGES or PIPELINE_STAGES.index(start) > PIPELINE_STAGES.index(end):
        raise ValueError("Invalid checkpoint pipeline range")
    if request.get("options", {}).get("detection_mode"):
        raise ValueError("不支持此检测模式，请刷新页面后使用重新检测")
    stages = PIPELINE_STAGES[PIPELINE_STAGES.index(start):PIPELINE_STAGES.index(end)+1]
    # A translation-only request still durably checkpoints prerequisite OCR.
    return ("ocr", *stages) if start == "translation" else stages


class BranchConflict(ValueError):
    pass


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(normalized(value), ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def record(row) -> dict:
    return {column.name: normalized(deepcopy(getattr(row, column.name))) for column in row.__table__.columns}


def decode(model, values: dict) -> dict:
    return {key: datetime.fromisoformat(value) if isinstance(model.__table__.c[key].type, DateTime)
            and isinstance(value, str) else deepcopy(value) for key, value in values.items()}


def capture_page(db: Session, page_id: str, *, compact_history: bool = True) -> dict:
    page = db.get(ImagePage, page_id, populate_existing=True)
    if page is None:
        raise ValueError("Workflow page no longer exists")
    project = db.get(Project, page.project_id, populate_existing=True)
    result = {"project": record(project), "page": record(page)}
    rows = db.scalars(select(TextRegion).where(TextRegion.image_id == page_id)
                      .execution_options(populate_existing=True)).all()
    result["regions"] = {row.id: record(row) for row in rows}
    ids = list(result["regions"])
    query = select(Translation).where(Translation.region_id.in_(ids))
    if compact_history:
        query = query.where(Translation.is_current.is_(True))
    result["translations"] = {row.id: record(row) for row in db.scalars(query)}
    result["revisions"] = {}
    for row in db.scalars(select(RegionRevision).where(RegionRevision.region_id.in_(ids))):
        value = record(row)
        if compact_history:
            # The stage algorithms consult only geometry/angle history. The
            # real history remains untouched; publish inserts only NEW rows.
            value["snapshot"] = {key: item for key, item in value["snapshot"].items() if key in GEOMETRY_HISTORY}
        result["revisions"][row.id] = value
    return result


def page_version(state: dict) -> str:
    # Progress/error timestamps must not invalidate a retry. Region timestamps
    # remain part of the fence, including an edit followed by an undo (ABA).
    return fingerprint({"page": {key: value for key, value in state["page"].items() if key not in VOLATILE_PAGE_FIELDS},
                        "regions": state["regions"]})


def current_page_version(db: Session, page_id: str) -> str:
    """Lightweight publication fence: no revision/translation history scan."""
    page = db.get(ImagePage, page_id, populate_existing=True)
    if page is None:
        raise ValueError("Workflow page no longer exists")
    regions = db.scalars(select(TextRegion).where(TextRegion.image_id == page_id)
                         .execution_options(populate_existing=True))
    return page_version({"page": record(page), "regions": {row.id: record(row) for row in regions}})


@contextmanager
def private_page(state: dict):
    """Disposable ORM adapter that cannot fall through to business tables.

    Temporary tables survive stage commits/rollbacks, but never their physical
    connection. NullPool guarantees cleanup on success, failure or cancellation.
    All mapped tables are shadowed, including configuration and task relations.
    """
    engine = create_database_engine(get_settings().database_url, poolclass=NullPool,
        connect_args={"options": "-c timezone=UTC -c search_path=pg_temp"})
    try:
        with engine.connect() as connection:
            for table in Base.metadata.sorted_tables:
                ddl = str(CreateTable(table).compile(dialect=engine.dialect))
                connection.exec_driver_sql(ddl.replace("CREATE TABLE", "CREATE TEMPORARY TABLE", 1))
            connection.commit()  # Rollbacks inside a stage must not drop its tables.
            with Session(bind=connection, expire_on_commit=False, autoflush=False) as db:
                db.add(Project(**decode(Project, state["project"])))
                db.flush()
                page = ImagePage(**decode(ImagePage, state["page"]))
                db.add(page)
                db.flush()
                for name, model in COLLECTIONS.items():
                    db.add_all(model(**decode(model, row)) for row in state[name].values())
                    db.flush()
                db.commit()
                yield db, page
    finally:
        engine.dispose()


def delta(before: dict, after: dict) -> dict:
    """Persist changed columns, not another copy of the page's old history."""
    result = {"page": {key: deepcopy(value) for key, value in after["page"].items()
                       if key != "updated_at" and value != before["page"].get(key)}}
    for collection in COLLECTIONS:
        previous, current = before[collection], after[collection]
        changes = {}
        for key, row in current.items():
            changed = deepcopy(row) if key not in previous else {
                column: deepcopy(value) for column, value in row.items()
                if column != "updated_at" and value != previous[key].get(column)
            }
            if changed:
                changes[key] = changed
        result[collection] = {"put": changes, "delete": sorted(set(previous) - set(current))}
    return result


def apply_delta(state: dict, changes: dict) -> dict:
    result = deepcopy(state)
    result["page"].update(deepcopy(changes.get("page", {})))
    for collection in COLLECTIONS:
        section = changes.get(collection, {})
        for key in section.get("delete", []):
            result[collection].pop(key, None)
        for key, value in section.get("put", {}).items():
            result[collection].setdefault(key, {}).update(deepcopy(value))
    return result


def merge_branches(base: dict, left: dict, right: dict) -> dict:
    """Three-way JSON merge. Conflicting edits fail instead of last-write wins."""
    def merge(original, a, b, path):
        if a == b:
            return a
        if a == original:
            return b
        if b == original:
            return a
        if original is _MISSING and isinstance(a, dict) and isinstance(b, dict):
            original = {}
        if all(isinstance(item, dict) for item in (original, a, b)):
            result = {}
            for key in set(original) | set(a) | set(b):
                value = merge(original.get(key, _MISSING), a.get(key, _MISSING), b.get(key, _MISSING), f"{path}/{key}")
                if value is not _MISSING:
                    result[key] = value
            return result
        # Error contains field paths only, never source text or credentials.
        raise BranchConflict(f"Workflow branch field conflict: {path}")
    return deepcopy(merge(base, left, right, "state"))

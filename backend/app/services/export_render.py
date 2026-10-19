"""Refresh export images using value-only workers and short, fenced writes."""
from copy import deepcopy
from uuid import uuid4

from app.core.transactions import retry_write
from app.models import ImagePage, ModelConfig, Project, RegionRevision, TextRegion
from app.pipeline.processor import PipelineProcessor
from app.services.editor_history import _archive_paths, ensure_page_idle
from app.services.execution import execution_runtime
from app.services.workflow_state import (
    capture_page,
    current_page_version,
    decode,
    delta,
    page_version,
    private_page,
    record,
)
from app.storage import get_storage
from fastapi import HTTPException
from sqlalchemy import select, update


def _render(state, models):
    # Private ORM objects are constructed and disposed inside this CPU worker.
    with private_page(state) as (db, page):
        db.add_all(ModelConfig(**decode(ModelConfig, row)) for row in models)
        db.commit()
        processor = PipelineProcessor(db)
        processor.settings = processor.settings.model_copy(update={"auto_provision_models": False})
        processor.render(page, None, region_id=None, output_tag=uuid4().hex)
        db.commit()
        return delta(state, capture_page(db, page.id, compact_history=False))


def refresh_export_page(db, page_id):
    page = db.get(ImagePage, page_id)
    ensure_page_idle(db, page)
    initial = capture_page(db, page_id)
    models = [record(row) for row in db.scalars(select(ModelConfig).where(
        ModelConfig.kind == "rendering", ModelConfig.enabled.is_(True)))]
    db.rollback()
    storage = get_storage()
    pixels = initial["page"]["width"] * initial["page"]["height"]
    jobs = [lambda: _render(initial, models)]
    _, changes = next(execution_runtime().map_cpu(jobs, check=lambda: None,
        estimate=64*1024**2 + pixels*24, kind="export:render", units=max(.25, pixels/1_000_000)))
    project_id = initial["page"]["project_id"]
    directory = storage.project_dir(project_id)
    for row in changes["revisions"]["put"].values():
        row["snapshot"] = _archive_paths(row["snapshot"], directory, directory / "history" / "assets")

    def save():
        db.execute(update(Project).where(Project.id == project_id).values(updated_at=Project.updated_at))
        current = db.scalar(select(ImagePage).where(ImagePage.id == page_id).with_for_update())
        if current is None:
            raise HTTPException(409, "导出期间图片已删除，请重试")
        ensure_page_idle(db, current)
        if current_page_version(db, page_id) != page_version(initial):
            raise HTTPException(409, "导出期间文本框已变化，未覆盖新编辑，请重试")
        for key, value in changes["page"].items():
            setattr(current, key, deepcopy(value))
        for identifier, patch in changes["regions"]["put"].items():
            region = db.get(TextRegion, identifier)
            for key, value in decode(TextRegion, patch).items():
                setattr(region, key, value)
        db.add_all(RegionRevision(**decode(RegionRevision, row)) for row in changes["revisions"]["put"].values())

    retry_write(db, save)
    db.expire_all()

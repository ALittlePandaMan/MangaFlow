"""Computed translations survive DB contention, not stale input versions."""
from __future__ import annotations

import pytest
from app.core.database import SessionLocal, engine
from app.models import TextRegion, Translation
from app.pipeline.processor import PipelineProcessor
from psycopg.errors import LockNotAvailable
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from test_translation_auto_ocr import run_translation, setup


@pytest.mark.parametrize("stale_input", [False, True])
def test_translation_retries_only_save_and_never_overwrites_stale_input(client, monkeypatch, stale_input):
    page, regions = setup(client, ["first", "second"])
    edited = max(regions, key=lambda region: region["id"])
    calls, failures = [], []

    class Translator:
        config = {}

        async def translate_regions(self, rows, **kwargs):
            calls.append(1)
            # This new session must be able to write during the slow request.
            if stale_input:
                with SessionLocal() as other:
                    other.get(TextRegion, edited["id"]).source_text = "concurrent edit"
                    other.commit()
            return {key: f"result {source}" for key, source in rows}

    provider = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda self, kind, requested:
                        (Translator(), "test") if kind == "translation" else provider(self, kind, requested))

    def fail_once(connection, cursor, statement, parameters, context, executemany):
        if not failures and not stale_input and statement.startswith("INSERT INTO translations"):
            failures.append(1)
            raise OperationalError("test", {}, LockNotAvailable("synthetic lock timeout"))

    event.listen(engine, "before_cursor_execute", fail_once)
    try:
        result, rows = run_translation(client, page, regions, "page")
    finally:
        event.remove(engine, "before_cursor_execute", fail_once)
    assert calls == [1]
    with SessionLocal() as db:
        count = db.scalar(select(func.count()).select_from(Translation).where(
            Translation.region_id.in_([region["id"] for region in regions]),
        ))
    if stale_input:
        assert result["status"] == "FAILED", result
        assert "文本框已变化" in result["error_message"] or "页面在计算期间发生变化" in result["error_message"]
        assert rows[edited["id"]]["source_text"] == "concurrent edit"
        assert all(not row["translated_text"] for row in rows.values())
        assert count == 0
    else:
        assert result["status"] == "COMPLETED", result
        assert failures == [1] and count == 2
        assert {row["translated_text"] for row in rows.values()} == {"result first", "result second"}


def test_failed_translation_keeps_previous_published_paths(client, monkeypatch):
    from app.models import ImagePage

    page, regions = setup(client, ["first"])
    with SessionLocal() as db:
        row = db.get(ImagePage, page["id"])
        row.rendered_path = "prior-render.png"
        row.text_layer_path = "prior-layer.png"
        db.commit()

    class Translator:
        config = {}

        async def translate_regions(self, *args, **kwargs):
            raise ValueError("simulated cloud failure")

    provider = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda self, kind, requested:
                        (Translator(), "test") if kind == "translation" else provider(self, kind, requested))
    result, _ = run_translation(client, page, regions, "page")
    assert result["status"] == "FAILED", result
    with SessionLocal() as db:
        row = db.get(ImagePage, page["id"])
        assert (row.rendered_path, row.text_layer_path) == ("prior-render.png", "prior-layer.png")


@pytest.mark.parametrize("value", ["", " \n　", "\u200b\ufeff", "\u200d", None, 12])
def test_provider_empty_values_cannot_overwrite_existing_translation(client, monkeypatch, value):
    page, regions = setup(client, ["first", "second"])
    for region in regions:
        assert client.patch(f"/api/regions/{region['id']}", json={"translated_text": "existing"}).status_code == 200

    class Translator:
        config = {}
        async def translate_regions(self, rows, **kwargs):
            return {key: "valid" if index == 0 else value for index, (key, _) in enumerate(rows)}

    provider = PipelineProcessor._provider
    monkeypatch.setattr(PipelineProcessor, "_provider", lambda self, kind, requested:
                        (Translator(), "test") if kind == "translation" else provider(self, kind, requested))
    result, rows = run_translation(client, page, regions, "page")
    assert result["status"] == "FAILED", result
    assert "空白或无效译文" in result["error_message"]
    assert all(row["translated_text"] == "existing" for row in rows.values())
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Translation).where(
            Translation.region_id.in_([region["id"] for region in regions]))) == 0

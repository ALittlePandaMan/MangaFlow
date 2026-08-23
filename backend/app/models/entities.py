from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import Orientation, PageStatus, RegionType, TaskStatus


def uuid4_str() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    cover_path: Mapped[str | None] = mapped_column(Text)
    source_language: Mapped[str] = mapped_column(String(32), default="ja")
    target_language: Mapped[str] = mapped_column(String(32), default="zh-CN")
    translation_context: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    pages: Mapped[list[ImagePage]] = relationship(back_populates="project", cascade="all, delete-orphan")
    tasks: Mapped[list[ProcessingTask]] = relationship(back_populates="project", cascade="all, delete-orphan")


class ImagePage(TimestampMixin, Base):
    __tablename__ = "image_pages"
    __table_args__ = (UniqueConstraint("project_id", "order_index", name="uq_page_order"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_path: Mapped[str] = mapped_column(Text, nullable=False)
    clean_path: Mapped[str | None] = mapped_column(Text)
    rendered_path: Mapped[str | None] = mapped_column(Text)
    text_layer_path: Mapped[str | None] = mapped_column(Text)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default=PageStatus.UPLOADED.value, index=True)
    current_stage: Mapped[str | None] = mapped_column(String(32))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    project: Mapped[Project] = relationship(back_populates="pages")
    regions: Mapped[list[TextRegion]] = relationship(
        back_populates="page", cascade="all, delete-orphan", order_by="TextRegion.reading_order"
    )
    tasks: Mapped[list[ProcessingTask]] = relationship(back_populates="page", cascade="all, delete-orphan")


class TextRegion(TimestampMixin, Base):
    __tablename__ = "text_regions"
    __table_args__ = (UniqueConstraint("image_id", "region_key", name="uq_region_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    image_id: Mapped[str] = mapped_column(ForeignKey("image_pages.id", ondelete="CASCADE"), index=True)
    region_key: Mapped[str] = mapped_column(String(24), nullable=False)
    polygon: Mapped[list[list[float]]] = mapped_column(JSON, default=list)
    bbox: Mapped[list[float]] = mapped_column(JSON, default=list)
    translated_polygon: Mapped[list[list[float]]] = mapped_column(JSON, default=list)
    translated_bbox: Mapped[list[float]] = mapped_column(JSON, default=list)
    pixel_mask_path: Mapped[str | None] = mapped_column(Text)
    source_text: Mapped[str] = mapped_column(Text, default="")
    translated_text: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    orientation: Mapped[str] = mapped_column(String(16), default=Orientation.VERTICAL.value)
    reading_order: Mapped[int] = mapped_column(Integer, default=0)
    panel_id: Mapped[str | None] = mapped_column(String(64))
    bubble_id: Mapped[str | None] = mapped_column(String(64))
    region_type: Mapped[str] = mapped_column(String(32), default=RegionType.BACKGROUND_COMPLEX.value)
    font_size: Mapped[float] = mapped_column(Float, default=28.0)
    font_family: Mapped[str] = mapped_column(String(200), default="Noto Sans CJK SC")
    font_weight: Mapped[int] = mapped_column(Integer, default=400)
    text_color: Mapped[str] = mapped_column(String(16), default="#111111")
    stroke_color: Mapped[str] = mapped_column(String(16), default="#ffffff")
    stroke_width: Mapped[float] = mapped_column(Float, default=0.0)
    alignment: Mapped[str] = mapped_column(String(16), default="center")
    line_spacing: Mapped[float] = mapped_column(Float, default=1.15)
    character_spacing: Mapped[float] = mapped_column(Float, default=0.0)
    rotation: Mapped[float] = mapped_column(Float, default=0.0)
    opacity: Mapped[float] = mapped_column(Float, default=1.0)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    visible: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true(), nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    review_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    layout_warning: Mapped[bool] = mapped_column(Boolean, default=False)
    layout_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    inpainted_path: Mapped[str | None] = mapped_column(Text)

    page: Mapped[ImagePage] = relationship(back_populates="regions")
    translations: Mapped[list[Translation]] = relationship(back_populates="region", cascade="all, delete-orphan")
    revisions: Mapped[list[RegionRevision]] = relationship(back_populates="region", cascade="all, delete-orphan")


class Translation(TimestampMixin, Base):
    __tablename__ = "translations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    region_id: Mapped[str] = mapped_column(ForeignKey("text_regions.id", ondelete="CASCADE"), index=True)
    source_text: Mapped[str] = mapped_column(Text, default="")
    translated_text: Mapped[str] = mapped_column(Text, default="")
    source_language: Mapped[str] = mapped_column(String(32), default="ja")
    target_language: Mapped[str] = mapped_column(String(32), default="zh-CN")
    provider: Mapped[str] = mapped_column(String(64), default="identity")
    model_name: Mapped[str] = mapped_column(String(128), default="fallback")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    raw_response: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    region: Mapped[TextRegion] = relationship(back_populates="translations")


class ModelConfig(TimestampMixin, Base):
    __tablename__ = "model_configs"
    __table_args__ = (UniqueConstraint("kind", "name", name="uq_model_kind_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(100))
    provider: Mapped[str] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    encrypted_api_key: Mapped[str | None] = mapped_column(Text)


class ProcessingTask(TimestampMixin, Base):
    __tablename__ = "processing_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    image_id: Mapped[str | None] = mapped_column(ForeignKey("image_pages.id", ondelete="CASCADE"), index=True)
    region_id: Mapped[str | None] = mapped_column(ForeignKey("text_regions.id", ondelete="CASCADE"), index=True)
    task_type: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(24), default=TaskStatus.QUEUED.value, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    current_stage: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    pause_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    project: Mapped[Project | None] = relationship(back_populates="tasks")
    page: Mapped[ImagePage | None] = relationship(back_populates="tasks")


class RegionRevision(Base):
    __tablename__ = "region_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    region_id: Mapped[str] = mapped_column(ForeignKey("text_regions.id", ondelete="CASCADE"), index=True)
    action: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    region: Mapped[TextRegion] = relationship(back_populates="revisions")

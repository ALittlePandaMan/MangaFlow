from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import Orientation, PipelineStage, RegionType, TaskStatus


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    source_language: str = "ja"
    target_language: str = "zh-CN"
    translation_context: dict[str, Any] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    source_language: str | None = None
    target_language: str | None = None
    translation_context: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None


class ProjectRead(ORMModel):
    id: str
    name: str
    description: str
    source_language: str
    target_language: str
    translation_context: dict[str, Any]
    settings: dict[str, Any]
    cover_url: str | None = None
    created_at: datetime
    updated_at: datetime
    page_count: int = 0


class ImageRead(ORMModel):
    id: str
    project_id: str
    filename: str
    width: int
    height: int
    order_index: int
    status: str
    current_stage: str | None
    error_message: str | None
    original_url: str | None = None
    clean_url: str | None = None
    rendered_url: str | None = None
    text_layer_url: str | None = None
    created_at: datetime
    updated_at: datetime


class RegionBase(BaseModel):
    polygon: list[list[float]] = Field(default_factory=list)
    bbox: list[float] = Field(default_factory=list)
    translated_polygon: list[list[float]] = Field(default_factory=list)
    translated_bbox: list[float] = Field(default_factory=list)
    source_text: str = ""
    translated_text: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    orientation: Orientation = Orientation.VERTICAL
    reading_order: int = 0
    panel_id: str | None = None
    bubble_id: str | None = None
    region_type: RegionType = RegionType.BACKGROUND_COMPLEX
    font_size: float = Field(default=28.0, ge=1.0, le=512.0)
    font_family: str = "Noto Sans CJK SC"
    font_weight: int = Field(default=400, ge=100, le=900)
    text_color: str = "#111111"
    stroke_color: str = "#ffffff"
    stroke_width: float = Field(default=0.0, ge=0.0, le=50.0)
    alignment: Literal["left", "center", "right"] = "center"
    line_spacing: float = Field(default=1.15, ge=0.5, le=4.0)
    character_spacing: float = Field(default=0.0, ge=-20.0, le=100.0)
    rotation: float = Field(default=0.0, ge=-360.0, le=360.0)
    opacity: float = Field(default=1.0, ge=0.0, le=1.0)
    locked: bool = False
    visible: bool = True

    @field_validator("bbox", "translated_bbox")
    @classmethod
    def validate_bbox(cls, value: list[float]) -> list[float]:
        if value and (len(value) != 4 or value[2] <= 0 or value[3] <= 0):
            raise ValueError("bbox must be [x, y, width, height] with positive dimensions")
        return value

    @field_validator("polygon", "translated_polygon")
    @classmethod
    def validate_polygon(cls, value: list[list[float]]) -> list[list[float]]:
        if value and (len(value) < 3 or any(len(point) != 2 for point in value)):
            raise ValueError("polygon needs at least three [x, y] points")
        return value


class RegionCreate(RegionBase):
    region_key: str | None = None


class RegionUpdate(BaseModel):
    polygon: list[list[float]] | None = None
    bbox: list[float] | None = None
    translated_polygon: list[list[float]] | None = None
    translated_bbox: list[float] | None = None
    source_text: str | None = None
    translated_text: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    orientation: Orientation | None = None
    reading_order: int | None = None
    panel_id: str | None = None
    bubble_id: str | None = None
    region_type: RegionType | None = None
    font_size: float | None = Field(default=None, ge=1.0, le=512.0)
    font_family: str | None = None
    font_weight: int | None = Field(default=None, ge=100, le=900)
    text_color: str | None = None
    stroke_color: str | None = None
    stroke_width: float | None = Field(default=None, ge=0.0, le=50.0)
    alignment: Literal["left", "center", "right"] | None = None
    line_spacing: float | None = Field(default=None, ge=0.5, le=4.0)
    character_spacing: float | None = Field(default=None, ge=-20.0, le=100.0)
    rotation: float | None = Field(default=None, ge=-360.0, le=360.0)
    opacity: float | None = Field(default=None, ge=0.0, le=1.0)
    locked: bool | None = None
    visible: bool | None = None


class RegionRead(ORMModel):
    id: str
    image_id: str
    region_key: str
    polygon: list[list[float]]
    bbox: list[float]
    translated_polygon: list[list[float]]
    translated_bbox: list[float]
    mask_url: str | None = None
    source_text: str
    translated_text: str
    confidence: float
    orientation: str
    reading_order: int
    panel_id: str | None
    bubble_id: str | None
    region_type: str
    font_size: float
    font_family: str
    font_weight: int
    text_color: str
    stroke_color: str
    stroke_width: float
    alignment: str
    line_spacing: float
    character_spacing: float
    rotation: float
    opacity: float
    locked: bool
    visible: bool
    needs_review: bool
    review_reasons: list[str]
    layout_warning: bool
    layout_data: dict[str, Any]
    updated_at: datetime


class ProcessRequest(BaseModel):
    start_stage: PipelineStage = PipelineStage.DETECTION
    # Full-page automation intentionally stops after OCR so users can verify
    # detected regions before any destructive background processing begins.
    end_stage: PipelineStage = PipelineStage.OCR
    provider: str | None = None
    force: bool = False
    options: dict[str, Any] = Field(default_factory=dict)


class RegionOCRRequest(BaseModel):
    provider: str | None = None
    orientation: Orientation | None = None
    crop_padding: int = Field(default=4, ge=0, le=256)
    force: bool = False


class TranslateRequest(BaseModel):
    provider: str | None = None
    style: str = "natural manga dialogue"
    glossary: dict[str, str] = Field(default_factory=dict)
    honorific_rules: str = "preserve when meaningful"
    onomatopoeia_strategy: str = "translate with concise equivalent"
    force: bool = False


class MaskOperation(BaseModel):
    operation: Literal["dilate", "erode", "blur", "expand", "clear"]
    amount: int = Field(default=3, ge=1, le=100)


class TaskRead(ORMModel):
    id: str
    project_id: str | None
    image_id: str | None
    region_id: str | None
    task_type: str
    status: TaskStatus | str
    progress: float
    current_stage: str | None
    message: str
    error_message: str | None
    payload: dict[str, Any]
    pause_requested: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ModelConfigCreate(BaseModel):
    kind: Literal["detection", "ocr", "translation", "inpainting", "rendering"]
    name: str
    provider: str
    enabled: bool = True
    is_default: bool = False
    config: dict[str, Any] = Field(default_factory=dict)
    api_key: str | None = None


class ModelConfigRead(ORMModel):
    id: str
    kind: str
    name: str
    provider: str
    enabled: bool
    is_default: bool
    config: dict[str, Any]
    has_api_key: bool = False
    capabilities: dict[str, Any] = Field(default_factory=dict)


class ModelDiscoveryRequest(BaseModel):
    base_url: str
    api_protocol: Literal["auto", "openai", "responses", "anthropic"] = "auto"
    api_key: str | None = None
    config_id: str | None = None


class BootstrapModelsRequest(BaseModel):
    stages: list[Literal["detection", "ocr", "translation", "inpainting", "rendering"]] | None = None
    preload: bool = True
    upgrade_fallbacks: bool = False


class ExportRequest(BaseModel):
    formats: list[Literal["translated", "clean", "text_layer", "json", "masks", "project"]] = Field(
        default_factory=lambda: ["translated", "clean", "json"]
    )


class BatchProcessRequest(ProcessRequest):
    image_ids: list[str] | None = None


class QualityIssue(BaseModel):
    project_id: str
    image_id: str
    region_id: str | None = None
    region_key: str | None = None
    code: str
    message: str
    severity: Literal["warning", "error"] = "warning"

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Runtime settings. Paths are absolute so workers are cwd-independent."""

    model_config = SettingsConfigDict(
        env_file=REPOSITORY_ROOT / ".env",
        env_prefix="MANGAFLOW_",
        extra="ignore",
    )

    app_name: str = "MangaFlow"
    debug: bool = False
    maintenance_mode: bool = False
    api_prefix: str = "/api"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    data_dir: Path = REPOSITORY_ROOT / "data"
    model_dir: Path = REPOSITORY_ROOT / "models"
    model_manifest_path: Path | None = None
    environment_file_path: Path = REPOSITORY_ROOT / ".env"
    database_url: str = "postgresql+psycopg://mangaflow@127.0.0.1:5432/mangaflow"
    max_upload_mb: int = 100
    max_container_upload_mb: int = Field(default=1024, ge=1)
    max_import_pages: int = Field(default=2000, ge=1)
    max_import_uncompressed_mb: int = Field(default=10240, ge=1)
    max_import_page_pixels: int = Field(default=50_000_000, ge=1)
    ocr_review_threshold: float = 0.65
    task_concurrency: int = Field(default=4, ge=1, le=8)
    runtime: Literal["gpu", "cpu"] = "gpu"
    execution_mode: Literal["auto", "fixed", "serial"] = "auto"
    cpu_workers: int = Field(default=4, ge=1, le=8)
    memory_reserve_mb: int = Field(default=1024, ge=256, le=16384)
    gpu_max_concurrency: int = Field(default=1, ge=1, le=2)
    gpu_memory_reserve_mb: int = Field(default=2048, ge=2048, le=16384)
    native_threads: int = Field(default=2, ge=1, le=16)
    auto_provision_models: bool = True
    text_segmentation_enabled: bool = True
    segmentation_backend: Literal["auto", "opencv", "onnx_cuda"] = "auto"
    repair_auto_retry: bool = True
    checkpoint_pipeline_enabled: bool = False
    parallel_repair_translation: bool = False
    checkpoint_gc_enabled: bool = False

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else [value]
            except json.JSONDecodeError:
                return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("model_manifest_path", mode="before")
    @classmethod
    def parse_optional_path(cls, value: Any) -> Any:
        return None if value is None or (isinstance(value, str) and not value.strip()) else value

    def ensure_directories(self) -> None:
        for directory in (
            self.data_dir,
            self.data_dir / "projects",
            self.data_dir / "fonts",
            self.data_dir / "cache",
            self.data_dir / "exports",
            self.model_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings

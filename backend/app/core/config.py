from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

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
    environment: str = "development"
    debug: bool = False
    api_prefix: str = "/api"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    data_dir: Path = REPOSITORY_ROOT / "data"
    model_dir: Path = REPOSITORY_ROOT / "models"
    model_manifest_path: Path | None = None
    environment_file_path: Path = REPOSITORY_ROOT / ".env"
    database_url: str | None = None
    max_upload_mb: int = 100
    ocr_review_threshold: float = 0.65
    task_concurrency: int = 1
    default_font_path: str | None = None
    auto_provision_models: bool = True

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

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'mangaflow.db').as_posix()}"

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

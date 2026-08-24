from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
connect_args = {"check_same_thread": False} if settings.resolved_database_url.startswith("sqlite") else {}
engine = create_engine(settings.resolved_database_url, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


if settings.resolved_database_url.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def initialize_database() -> None:
    # Importing registers every mapped class before metadata creation.
    from app.models import entities  # noqa: F401

    Base.metadata.create_all(bind=engine)
    # create_all does not add columns to databases created by older releases.
    # Keep this small compatibility migration here until formal migrations are introduced.
    region_columns = {column["name"] for column in inspect(engine).get_columns("text_regions")}
    with engine.begin() as connection:
        if "visible" not in region_columns:
            connection.execute(text("ALTER TABLE text_regions ADD COLUMN visible BOOLEAN NOT NULL DEFAULT TRUE"))
        if "translated_polygon" not in region_columns:
            connection.execute(text("ALTER TABLE text_regions ADD COLUMN translated_polygon JSON NOT NULL DEFAULT '[]'"))
            connection.execute(text("UPDATE text_regions SET translated_polygon = polygon"))
        if "translated_bbox" not in region_columns:
            connection.execute(text("ALTER TABLE text_regions ADD COLUMN translated_bbox JSON NOT NULL DEFAULT '[]'"))
            connection.execute(text("UPDATE text_regions SET translated_bbox = bbox"))
        if "perspective_warp" not in region_columns:
            connection.execute(
                text("ALTER TABLE text_regions ADD COLUMN perspective_warp BOOLEAN NOT NULL DEFAULT FALSE")
            )

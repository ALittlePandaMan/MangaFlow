from __future__ import annotations

from collections.abc import Generator

from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings
from app.core.db_engine import create_database_engine


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine = create_database_engine(settings.resolved_database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def initialize_database() -> None:
    from app.core.migrations import upgrade_database

    upgrade_database(engine)
    from app.services.infra.detection_migration import migrate_retired_detection_configs

    with SessionLocal() as db:
        migrate_retired_detection_configs(db)

"""Programmatic Alembic runner, also usable with an isolated target engine."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine


def migration_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    return config


def upgrade_database(engine: Engine) -> None:
    # One transactional lock serializes schema adoption across startup races.
    with engine.connect() as connection:
        connection.begin()
        connection.exec_driver_sql("SELECT pg_advisory_xact_lock(74125789017831)")
        try:
            config = migration_config()
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

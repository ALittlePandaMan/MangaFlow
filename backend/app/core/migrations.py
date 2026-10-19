"""Programmatic Alembic runner, also usable with an isolated target engine."""
from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect
from sqlalchemy.engine import Engine

HEAD = "20261018_05"


def migration_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    return config


def database_revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        if not inspect(connection).has_table("alembic_version"):
            return None
        return MigrationContext.configure(connection).get_current_revision()


def upgrade_database(engine: Engine) -> None:
    # One transactional lock serializes schema adoption across startup races.
    with engine.connect() as connection:
        if engine.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA journal_mode=WAL")
            connection.commit()
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        else:
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

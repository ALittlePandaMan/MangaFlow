"""Current PostgreSQL schema and checkpoint-preserving upgrade regressions."""
from datetime import datetime, timezone

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from app.core.database import Base
from app.core.migrations import HEAD, database_revision, migration_config, upgrade_database
from app.models import ProcessingTask, StageCheckpoint, TaskWorkflow
from sqlalchemy import MetaData
from sqlalchemy.orm import Session


def test_fresh_and_repeated_schema_upgrade_matches_orm(target):
    upgrade_database(target)
    upgrade_database(target)
    assert database_revision(target) == HEAD
    with target.connect() as connection:
        context = MigrationContext.configure(connection)
        assert compare_metadata(context, Base.metadata) == []


def test_batch_migration_preserves_existing_task_checkpoints(target):
    with target.begin() as connection:
        config = migration_config()
        config.attributes["connection"] = connection
        command.upgrade(config, "20261018_04")
        previous = MetaData()
        previous.reflect(connection)
        now = datetime.now(timezone.utc)
        connection.execute(previous.tables["processing_tasks"].insert().values(
            id="old-task", task_type="full", status="FAILED", progress=.6, message="old failure",
            payload={}, pause_requested=False, execution_generation=1, created_at=now, updated_at=now,
        ))
        connection.execute(TaskWorkflow.__table__.insert().values(
            id="old-workflow", task_id="old-task", generation=1, input_fingerprint="input",
            request_fingerprint="request", input_state={}, policy={},
        ))
        connection.execute(StageCheckpoint.__table__.insert().values(
            id="old-checkpoint", workflow_id="old-workflow", stage="translation", attempt=1,
            generation=1, input_fingerprint="stage-input", status="SUCCEEDED", cloud_request=True,
            result={"translated_text":"paid result must survive"},
        ))
    upgrade_database(target)
    with Session(target) as db:
        task = db.get(ProcessingTask, "old-task")
        assert task.status == "FAILED" and task.active_stages == [] and task.batch_id is None
        assert db.get(StageCheckpoint, "old-checkpoint").result == {"translated_text":"paid result must survive"}
    with target.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []

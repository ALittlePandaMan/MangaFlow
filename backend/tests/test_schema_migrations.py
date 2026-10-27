"""Current PostgreSQL schema and checkpoint-preserving upgrade regressions."""
from datetime import datetime, timezone

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from app.core.database import Base
from app.core.migrations import migration_config, upgrade_database
from app.models import ProcessingTask, StageCheckpoint, TaskWorkflow
from sqlalchemy import MetaData
from sqlalchemy.orm import Session


def test_fresh_and_repeated_schema_upgrade_matches_orm(target):
    upgrade_database(target)
    upgrade_database(target)
    with target.connect() as connection:
        context = MigrationContext.configure(connection)
        assert context.get_current_revision() == ScriptDirectory.from_config(migration_config()).get_current_head()
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


def test_mcp_workflow_upgrade_keeps_existing_uploads_and_operation_receipts(target):
    from app.models import MCPAsset, MCPOperation
    with target.begin() as connection:
        config = migration_config()
        config.attributes["connection"] = connection
        command.upgrade(config, "20261023_08")
        previous = MetaData()
        previous.reflect(connection)
        now = datetime.now(timezone.utc)
        connection.execute(previous.tables["mcp_assets"].insert().values(id="existing-upload", filename="漫画.cbz",
            size=123, status="AVAILABLE", expected_size=123, created_at=now, updated_at=now))
        connection.execute(previous.tables["mcp_operations"].insert().values(id="existing-operation", fingerprint="a"*64,
            status="COMPLETED", result={"structuredContent": {"id": "already-imported"}}, created_at=now))
    upgrade_database(target)
    with Session(target) as db:
        assert db.get(MCPAsset, "existing-upload").evidence == {}
        receipt = db.get(MCPOperation, "existing-operation")
        assert receipt.workflow_run_id is None and receipt.result["structuredContent"]["id"] == "already-imported"
    with target.begin() as connection:
        config = migration_config()
        config.attributes["connection"] = connection
        command.downgrade(config, "20261023_08")
    upgrade_database(target)
    with target.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []


def test_coordination_upgrade_preserves_existing_reviews_and_rounds(target):
    from app.models import MCPWorkflowPage, Project
    with target.begin() as connection:
        config = migration_config()
        config.attributes["connection"] = connection
        command.upgrade(config, "20261024_09")
        previous = MetaData()
        previous.reflect(connection)
        now = datetime.now(timezone.utc)
        connection.execute(Project.__table__.insert().values(id="parallel-old-project", name="Existing", created_at=now, updated_at=now))
        connection.execute(previous.tables["mcp_workflow_runs"].insert().values(id="parallel-old-run", project_id="parallel-old-project",
            workflow_id="translate_and_review", workflow_version="2.1.1", page_ids=["old-page"], options={}, created_at=now, updated_at=now))
        connection.execute(previous.tables["mcp_workflow_pages"].insert().values(id="parallel-old-receipt", run_id="parallel-old-run",
            image_id="old-page", preflight={"region_ids":["old-region"]}, final_review={"outcome":"issues","version":"old-version"},
            correction_rounds=2, correction_open=True, created_at=now, updated_at=now))
    upgrade_database(target)
    with Session(target) as db:
        receipt = db.get(MCPWorkflowPage, "parallel-old-receipt")
        assert receipt.assignment == {} and receipt.correction_rounds == 2 and receipt.correction_open
        assert receipt.preflight == {"region_ids":["old-region"]}
        assert receipt.final_review == {"outcome":"issues","version":"old-version"}

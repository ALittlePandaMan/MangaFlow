"""Durable page-workflow inputs, generation fences, and stage checkpoints."""
import sqlalchemy as sa
from alembic import op

revision = "20261018_04"
down_revision = "20261018_03"
branch_labels = None
depends_on = None


def _create(name, *columns):
    inspector = sa.inspect(op.get_bind())
    if name not in inspector.get_table_names():
        op.create_table(name, *columns)
    else:
        expected = {column.name for column in columns if isinstance(column, sa.Column)}
        actual = {column["name"] for column in inspector.get_columns(name)}
        if missing := expected - actual:
            raise ValueError(f"Unsupported checkpoint schema: {name} missing {sorted(missing)}")


def _index(name, table, column):
    if name not in {item["name"] for item in sa.inspect(op.get_bind()).get_indexes(table)}:
        op.create_index(name, table, [column])


def upgrade():
    if "execution_generation" not in {item["name"] for item in sa.inspect(op.get_bind()).get_columns("processing_tasks")}:
        op.add_column("processing_tasks", sa.Column("execution_generation", sa.Integer(), nullable=False, server_default="0"))
    _create("task_workflows",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_id", sa.String(36), sa.ForeignKey("processing_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("input_state", sa.JSON(), nullable=False),
        sa.Column("policy", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    _index("ix_task_workflows_task_id", "task_workflows", "task_id")
    _create("stage_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("workflow_id", sa.String(36), sa.ForeignKey("task_workflows.id", ondelete="CASCADE"), nullable=False),
        sa.Column("stage", sa.String(32), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("input_fingerprint", sa.String(64), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("cloud_request", sa.Boolean(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("artifacts", sa.JSON(), nullable=False),
        sa.Column("error_kind", sa.String(48), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workflow_id", "stage", "attempt", name="uq_workflow_stage_attempt"),
    )
    _index("ix_stage_checkpoints_workflow_id", "stage_checkpoints", "workflow_id")


def downgrade():
    raise RuntimeError("Destructive schema downgrades are disabled; restore a verified backup instead")

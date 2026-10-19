"""Frozen batch context and dependency-aware task admission."""
import sqlalchemy as sa
from alembic import op

revision = "20261018_05"
down_revision = "20261018_04"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if "processing_batches" not in sa.inspect(bind).get_table_names():
        op.create_table("processing_batches",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
            sa.Column("context_mode", sa.String(24), nullable=False),
            sa.Column("policy", sa.JSON(), nullable=False),
            sa.Column("contexts", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    columns = {item["name"] for item in sa.inspect(bind).get_columns("processing_tasks")}
    if "batch_id" not in columns:
        if bind.dialect.name == "sqlite":
            # Nullable inline REFERENCES is supported without rebuilding the
            # parent task table (a rebuild could cascade-delete its checkpoints).
            op.execute("ALTER TABLE processing_tasks ADD COLUMN batch_id VARCHAR(36) REFERENCES processing_batches(id)")
        else:
            op.add_column("processing_tasks", sa.Column("batch_id", sa.String(36),
                sa.ForeignKey("processing_batches.id"), nullable=True))
    for column in (
        sa.Column("dependency_task_id", sa.String(36), nullable=True),
        sa.Column("dependency_context", sa.JSON(), nullable=True),
        sa.Column("waiting_reason", sa.String(64), nullable=True),
        sa.Column("error_kind", sa.String(64), nullable=True),
        sa.Column("active_stages", sa.JSON(), nullable=False, server_default="[]"),
    ):
        if column.name not in columns:
            op.add_column("processing_tasks", column)
    for table, column in (("processing_batches", "project_id"), ("processing_tasks", "batch_id"), ("processing_tasks", "dependency_task_id")):
        name = f"ix_{table}_{column}"
        if name not in {item["name"] for item in sa.inspect(bind).get_indexes(table)}:
            op.create_index(name, table, [column])


def downgrade():
    raise RuntimeError("Destructive schema downgrades are disabled; restore a verified backup instead")

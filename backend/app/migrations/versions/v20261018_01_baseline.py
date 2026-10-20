"""Create the initial PostgreSQL schema; later revisions evolve it."""
from alembic import op

from app.migrations.baseline import metadata

revision = "20261018_01"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    for table in metadata.sorted_tables:
        if table.name != "task_execution_runs":
            table.create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("Destructive schema downgrades are disabled; restore a verified backup instead")

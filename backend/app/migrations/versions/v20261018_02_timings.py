"""Persistent task-attempt timing table (adopts foundation builds too)."""
import sqlalchemy as sa
from alembic import op

from app.migrations.baseline import metadata

revision = "20261018_02"
down_revision = "20261018_01"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    table = metadata.tables["task_execution_runs"]
    table.create(bind, checkfirst=True)
    actual = {item["name"] for item in sa.inspect(bind).get_columns(table.name)}
    if missing := set(table.columns.keys()) - actual:
        raise ValueError(f"Unsupported timing schema: missing {sorted(missing)}")
    for index in table.indexes:
        index.create(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("Destructive schema downgrades are disabled; restore a verified backup instead")

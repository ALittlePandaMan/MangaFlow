"""Preserve the project cover pointer while upgrading PostgreSQL."""
import sqlalchemy as sa
from alembic import op

revision = "20261018_03"
down_revision = "20261018_02"
branch_labels = None
depends_on = None


def upgrade():
    columns = {item["name"] for item in sa.inspect(op.get_bind()).get_columns("projects")}
    if "cover_path" not in columns:
        op.add_column("projects", sa.Column("cover_path", sa.Text(), nullable=True))


def downgrade():
    raise RuntimeError("Destructive schema downgrades are disabled; restore a verified backup instead")

"""Persist page ownership without a separate agent/task scheduler."""
import sqlalchemy as sa
from alembic import op

revision = "20261025_10"
down_revision = "20261024_09"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("mcp_workflow_pages", sa.Column("assignment", sa.JSON(), nullable=False, server_default="{}"))


def downgrade():
    op.drop_column("mcp_workflow_pages", "assignment")

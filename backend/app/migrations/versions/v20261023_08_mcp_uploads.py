"""Track external uploads in the shared activity panel."""
import sqlalchemy as sa
from alembic import op

revision = "20261023_08"
down_revision = "20261023_07"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("mcp_assets", sa.Column("status", sa.String(24), nullable=False, server_default="AVAILABLE"))
    op.add_column("mcp_assets", sa.Column("expected_size", sa.BigInteger(), nullable=False, server_default="0"))


def downgrade():
    op.drop_column("mcp_assets", "expected_size")
    op.drop_column("mcp_assets", "status")

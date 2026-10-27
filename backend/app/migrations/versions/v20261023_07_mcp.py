"""Universal MCP access and independent agent review records."""
import sqlalchemy as sa
from alembic import op

revision = "20261023_07"
down_revision = "20261023_06"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("mcp_settings", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("enabled", sa.Boolean(), nullable=False), sa.Column("encrypted_token", sa.Text(), nullable=False),
        sa.Column("public_url", sa.Text(), nullable=False), sa.Column("allowed_hosts", sa.JSON(), nullable=False),
        sa.Column("allowed_origins", sa.JSON(), nullable=False))
    op.create_table("mcp_operations", sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("fingerprint", sa.String(64), nullable=False), sa.Column("status", sa.String(24), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("mcp_assets", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("filename", sa.Text(), nullable=False), sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("agent_review_issues", sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), nullable=False), sa.Column("image_id", sa.String(36), nullable=False),
        sa.Column("region_id", sa.String(36)), sa.Column("page_version", sa.String(64), nullable=False),
        sa.Column("category", sa.String(48), nullable=False), sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(24), nullable=False), sa.Column("author", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False))
    for column in ("project_id", "image_id"):
        op.create_index(f"ix_agent_review_issues_{column}", "agent_review_issues", [column])


def downgrade():
    for table in ("agent_review_issues", "mcp_assets", "mcp_operations", "mcp_settings"):
        op.drop_table(table)

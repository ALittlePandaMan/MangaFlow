"""Official agent workflows and version-bound visual review receipts."""
import sqlalchemy as sa
from alembic import op

revision = "20261024_09"
down_revision = "20261023_08"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("mcp_operations", "agent_review_issues"):
        op.add_column(table, sa.Column("workflow_run_id", sa.String(36), nullable=True))
        op.create_index(f"ix_{table}_workflow_run_id", table, ["workflow_run_id"])
    op.add_column("mcp_assets", sa.Column("evidence", sa.JSON(), nullable=False, server_default="{}"))
    from app.models import MCPWorkflowRun
    MCPWorkflowRun.__table__.create(op.get_bind())
    # Historical schema: later ORM columns belong to their own migrations.
    op.create_table("mcp_workflow_pages",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("run_id", sa.String(36), sa.ForeignKey("mcp_workflow_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("image_id", sa.String(36), nullable=False),
        sa.Column("preflight", sa.JSON(), nullable=False),
        sa.Column("final_review", sa.JSON(), nullable=False),
        sa.Column("correction_rounds", sa.Integer(), nullable=False),
        sa.Column("correction_open", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("run_id", "image_id", name="uq_mcp_workflow_page"))
    op.create_index("ix_mcp_workflow_pages_run_id", "mcp_workflow_pages", ["run_id"])


def downgrade():
    op.drop_table("mcp_workflow_pages")
    op.drop_table("mcp_workflow_runs")
    op.drop_column("mcp_assets", "evidence")
    for table in ("mcp_operations", "agent_review_issues"):
        op.drop_index(f"ix_{table}_workflow_run_id", table_name=table)
        op.drop_column(table, "workflow_run_id")

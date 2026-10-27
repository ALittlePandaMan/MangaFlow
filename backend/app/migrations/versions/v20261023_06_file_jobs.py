"""Persistent, independent import/export queue."""
import sqlalchemy as sa
from alembic import op

revision = "20261023_06"
down_revision = "20261018_05"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("file_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("progress", sa.Float(), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_file_jobs_project_id", "file_jobs", ["project_id"])
    op.create_index("ix_file_jobs_status", "file_jobs", ["status"])


def downgrade():
    op.drop_table("file_jobs")

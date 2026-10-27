"""MCP credentials, replay protection and agent-authored quality observations."""
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.entities import TimestampMixin, utcnow, uuid4_str


class MCPSettings(Base):
    __tablename__ = "mcp_settings"
    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    encrypted_token: Mapped[str] = mapped_column(Text)
    public_url: Mapped[str] = mapped_column(Text, default="")
    allowed_hosts: Mapped[list] = mapped_column(JSON, default=lambda: ["localhost", "127.0.0.1", "[::1]"])
    allowed_origins: Mapped[list] = mapped_column(JSON, default=list)


class MCPOperation(Base):
    __tablename__ = "mcp_operations"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="RUNNING")
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    workflow_run_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class MCPAsset(TimestampMixin, Base):
    __tablename__ = "mcp_assets"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    filename: Mapped[str] = mapped_column(Text)
    size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24), default="AVAILABLE", server_default="AVAILABLE")
    expected_size: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")


class AgentReviewIssue(TimestampMixin, Base):
    __tablename__ = "agent_review_issues"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    image_id: Mapped[str] = mapped_column(String(36), index=True)
    region_id: Mapped[str | None] = mapped_column(String(36))
    page_version: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="open")
    author: Mapped[str] = mapped_column(String(100), default="agent")
    workflow_run_id: Mapped[str | None] = mapped_column(String(36), index=True)


class MCPWorkflowRun(TimestampMixin, Base):
    __tablename__ = "mcp_workflow_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    workflow_id: Mapped[str] = mapped_column(String(64))
    workflow_version: Mapped[str] = mapped_column(String(24))
    page_ids: Mapped[list] = mapped_column(JSON)
    options: Mapped[dict] = mapped_column(JSON, default=dict)


class MCPWorkflowPage(TimestampMixin, Base):
    __tablename__ = "mcp_workflow_pages"
    __table_args__ = (UniqueConstraint("run_id", "image_id", name="uq_mcp_workflow_page"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid4_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("mcp_workflow_runs.id", ondelete="CASCADE"), index=True)
    # Deliberately retain a receipt when its image is deleted during a run.
    image_id: Mapped[str] = mapped_column(String(36))
    preflight: Mapped[dict] = mapped_column(JSON, default=dict)
    final_review: Mapped[dict] = mapped_column(JSON, default=dict)
    correction_rounds: Mapped[int] = mapped_column(Integer, default=0)
    correction_open: Mapped[bool] = mapped_column(Boolean, default=False)
    assignment: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")

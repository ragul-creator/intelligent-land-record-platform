"""Phase E.2 persisted human-review queue model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.foundation import TimestampedModel


class ReviewTask(TimestampedModel, Base):
    """Project-scoped human verification work item.

    The task lifecycle is intentionally small: OPEN while reviewer work or
    revalidation is pending, RESOLVED after a terminal reviewer outcome.
    Reviewer actions are preserved in immutable audit logs.
    """

    __tablename__ = "review_tasks"
    __table_args__ = (
        CheckConstraint("queue_type IN ('DOCUMENT', 'GIS')", name="ck_review_tasks_queue_type"),
        CheckConstraint("severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH')", name="ck_review_tasks_severity"),
        CheckConstraint("status IN ('OPEN', 'RESOLVED')", name="ck_review_tasks_status"),
        CheckConstraint("blocking_issue_count >= 0", name="ck_review_tasks_blocking_issue_count"),
        Index("ix_review_tasks_project_status", "project_id", "status"),
        Index("ix_review_tasks_project_queue_status", "project_id", "queue_type", "status"),
        Index("ix_review_tasks_assignee_status", "assignee_user_id", "status"),
        Index("ix_review_tasks_target", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    queue_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), server_default="OPEN", nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_refs_json: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", nullable=False)
    blocking_issue_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    escalated: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    resolution_action: Mapped[str | None] = mapped_column(String(32))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )

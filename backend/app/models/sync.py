"""Offline-first synchronization persistence models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BIGINT,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SyncOperation(Base):
    """Tracks client-submitted offline mutations for global idempotency and audit."""

    __tablename__ = "sync_operations"
    __table_args__ = (
        CheckConstraint(
            "operation_type IN ('PARCEL_VERSION_CREATE', 'FIELD_CORRECTION_CREATE', 'REVIEW_TASK_UPDATE', 'RECORD_LINK_RESOLVE')",
            name="ck_sync_operations_type",
        ),
        CheckConstraint(
            "status IN ('APPLIED', 'DUPLICATE', 'CONFLICT', 'REJECTED')",
            name="ck_sync_operations_status",
        ),
        Index("ix_sync_operations_project_created", "project_id", "created_at"),
        Index("ix_sync_operations_project_entity", "project_id", "entity_id"),
        Index("ix_sync_operations_user_id", "user_id"),
        Index("ix_sync_operations_client_id", "client_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    client_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    operation_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=func.text("'{}'::jsonb"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    conflict_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SyncChange(Base):
    """Monotonic append-only change feed event stream for project delta sync."""

    __tablename__ = "sync_changes"
    __table_args__ = (
        Index("ix_sync_changes_project_cursor", "project_id", "id"),
        Index("ix_sync_changes_entity", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(
        BIGINT,
        Identity(start=1, increment=1),
        primary_key=True,
        autoincrement=True,
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    change_type: Mapped[str] = mapped_column(String(32), nullable=False)
    server_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

"""Project-scoped, evidence-preserving links between validated records and parcels."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKeyConstraint, Index, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.foundation import TimestampedModel


class RecordParcelLink(TimestampedModel, Base):
    """A workflow association, never a claim of statutory ownership proof."""

    __tablename__ = "record_parcel_links"
    __table_args__ = (
        CheckConstraint(
            "link_status IN ('SUGGESTED', 'REVIEW_REQUIRED', 'CONFIRMED', 'REJECTED')",
            name="ck_record_parcel_links_status",
        ),
        CheckConstraint(
            "link_method IN ('EXACT_SURVEY_IDENTIFIER', 'ATTRIBUTE_MATCH', 'SPATIAL_CONTEXT', 'MANUAL')",
            name="ck_record_parcel_links_method",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ck_record_parcel_links_confidence",
        ),
        ForeignKeyConstraint(
            ["project_id", "document_id"],
            ["documents.project_id", "documents.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_id", "parcel_id"],
            ["parcels.project_id", "parcels.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_id", "document_validation_result_id"],
            ["document_validation_results.document_id", "document_validation_results.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(["review_task_id"], ["review_tasks.id"], ondelete="SET NULL"),
        UniqueConstraint("review_task_id", name="uq_record_parcel_links_review_task_id"),
        Index("ix_record_parcel_links_project_document", "project_id", "document_id"),
        Index("ix_record_parcel_links_project_parcel", "project_id", "parcel_id"),
        Index(
            "uq_record_parcel_links_active_candidate",
            "document_validation_result_id",
            "parcel_id",
            unique=True,
            postgresql_where=text("link_status IN ('SUGGESTED', 'REVIEW_REQUIRED', 'CONFIRMED')"),
        ),
        Index(
            "uq_record_parcel_links_confirmed_validation",
            "document_validation_result_id",
            unique=True,
            postgresql_where=text("link_status = 'CONFIRMED'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    document_validation_result_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    parcel_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    link_status: Mapped[str] = mapped_column(String(32), nullable=False)
    link_method: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    rationale_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", nullable=False)
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", nullable=False)
    created_by_user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    reviewed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    review_reason: Mapped[str | None] = mapped_column(Text)

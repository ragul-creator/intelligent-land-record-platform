"""Append-only persistence records for the Phase F.4 Document AI workflow."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.foundation import TimestampedModel


class Document(TimestampedModel, Base):
    """Project-scoped Document AI workflow state over an immutable source File."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('UPLOADED', 'QUEUED', 'PROCESSING', 'EXTRACTED', 'VALIDATING', 'REVIEW_REQUIRED', 'VALIDATED', 'FAILED')",
            name="ck_documents_status",
        ),
        Index("ix_documents_project_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    file_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("files.id", ondelete="RESTRICT"), unique=True, nullable=False)
    uploaded_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(32), server_default="UPLOADED", nullable=False)


class DocumentProcessingJob(TimestampedModel, Base):
    """Document-specific provenance for a generic ProcessingJob."""

    __tablename__ = "document_processing_jobs"
    __table_args__ = (
        CheckConstraint("job_type IN ('DOCUMENT_AI_PROCESS', 'DOCUMENT_REVALIDATE')", name="ck_document_processing_jobs_type"),
        Index("ix_document_processing_jobs_document_created", "document_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("processing_jobs.id", ondelete="CASCADE"), primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    requested_languages_json: Mapped[list[str]] = mapped_column(JSONB, server_default="[]", nullable=False)
    processing_version: Mapped[int] = mapped_column(Integer, nullable=False)
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    output_refs_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", nullable=False)


class DocumentOcrResultRecord(Base):
    """Immutable F.1 payload, including page text and token-level provenance."""

    __tablename__ = "document_ocr_results"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_ocr_results_document_version"),
        UniqueConstraint("processing_job_id", name="uq_document_ocr_results_processing_job"),
        Index("ix_document_ocr_results_document_version", "document_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    processing_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("processing_jobs.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    requested_languages_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    project_tested_languages_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    engine: Mapped[str] = mapped_column(String(100), nullable=False)
    engine_version: Mapped[str | None] = mapped_column(String(255))
    model_version: Mapped[str | None] = mapped_column(String(255))
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float | None] = mapped_column()
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DocumentExtractedField(Base):
    """One F.2 candidate. Corrections are stored separately and never mutate it."""

    __tablename__ = "document_extracted_fields"
    __table_args__ = (
        UniqueConstraint("ocr_result_id", "candidate_index", name="uq_document_extracted_fields_result_index"),
        Index("ix_document_extracted_fields_document_name", "document_id", "field_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    ocr_result_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("document_ocr_results.id", ondelete="RESTRICT"), nullable=False)
    candidate_index: Mapped[int] = mapped_column(Integer, nullable=False)
    field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    original_value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value_json: Mapped[Any | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column()
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    bounding_box_json: Mapped[dict[str, int] | None] = mapped_column(JSONB)
    source_id: Mapped[str] = mapped_column(String(1024), nullable=False)
    model_version: Mapped[str | None] = mapped_column(String(255))
    extractor_version: Mapped[str] = mapped_column(String(255), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DocumentValidationResultRecord(Base):
    """Immutable F.3 report plus an optional single E.2 task for this version."""

    __tablename__ = "document_validation_results"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_document_validation_results_document_version"),
        UniqueConstraint("processing_job_id", name="uq_document_validation_results_processing_job"),
        Index("ix_document_validation_results_document_version", "document_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    ocr_result_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("document_ocr_results.id", ondelete="RESTRICT"), nullable=False)
    processing_job_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("processing_jobs.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    validation_version: Mapped[str] = mapped_column(String(255), nullable=False)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence_summary_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    checks_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    review_task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("review_tasks.id", ondelete="SET NULL"), unique=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DocumentFieldCorrection(Base):
    """Append-only human correction referencing, rather than replacing, F.2 evidence."""

    __tablename__ = "document_field_corrections"
    __table_args__ = (
        UniqueConstraint("extracted_field_id", "version", name="uq_document_field_corrections_field_version"),
        Index("ix_document_field_corrections_document_field", "document_id", "extracted_field_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False)
    extracted_field_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("document_extracted_fields.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

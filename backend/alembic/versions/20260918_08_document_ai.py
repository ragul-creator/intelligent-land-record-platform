"""Add Phase F.4 Document AI persistence.

Revision ID: 20260918_08
Revises: 20260917_07
Create Date: 2026-09-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260918_08"
down_revision = "20260917_07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False), sa.Column("uploaded_by_user_id", sa.Uuid()),
        sa.Column("status", sa.String(32), server_default="UPLOADED", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('UPLOADED', 'QUEUED', 'PROCESSING', 'EXTRACTED', 'VALIDATING', 'REVIEW_REQUIRED', 'VALIDATED', 'FAILED')", name="ck_documents_status"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.UniqueConstraint("file_id"),
    )
    op.create_index("ix_documents_project_status", "documents", ["project_id", "status"])
    op.create_table(
        "document_processing_jobs",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("requested_languages_json", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False), sa.Column("processing_version", sa.Integer(), nullable=False),
        sa.Column("job_type", sa.String(32), nullable=False), sa.Column("requested_by_user_id", sa.Uuid()), sa.Column("output_refs_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("job_type IN ('DOCUMENT_AI_PROCESS', 'DOCUMENT_REVALIDATE')", name="ck_document_processing_jobs_type"),
        sa.ForeignKeyConstraint(["id"], ["processing_jobs.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_document_processing_jobs_document_created", "document_processing_jobs", ["document_id", "created_at"])
    op.create_table(
        "document_ocr_results",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("document_id", sa.Uuid(), nullable=False), sa.Column("processing_job_id", sa.Uuid(), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("status", sa.String(32), nullable=False),
        sa.Column("requested_languages_json", postgresql.JSONB(), nullable=False), sa.Column("project_tested_languages_json", postgresql.JSONB(), nullable=False), sa.Column("engine", sa.String(100), nullable=False), sa.Column("engine_version", sa.String(255)), sa.Column("model_version", sa.String(255)), sa.Column("page_count", sa.Integer(), nullable=False), sa.Column("confidence", sa.Float()), sa.Column("payload_json", postgresql.JSONB(), nullable=False), sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["processing_job_id"], ["processing_jobs.id"], ondelete="RESTRICT"), sa.UniqueConstraint("document_id", "version", name="uq_document_ocr_results_document_version"), sa.UniqueConstraint("processing_job_id", name="uq_document_ocr_results_processing_job"),
    )
    op.create_index("ix_document_ocr_results_document_version", "document_ocr_results", ["document_id", "version"])
    op.create_table(
        "document_extracted_fields",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("document_id", sa.Uuid(), nullable=False), sa.Column("ocr_result_id", sa.Uuid(), nullable=False), sa.Column("candidate_index", sa.Integer(), nullable=False), sa.Column("field_name", sa.String(100), nullable=False), sa.Column("original_value", sa.Text(), nullable=False), sa.Column("normalized_value_json", postgresql.JSONB()), sa.Column("confidence", sa.Float()), sa.Column("page_number", sa.Integer(), nullable=False), sa.Column("bounding_box_json", postgresql.JSONB()), sa.Column("source_id", sa.String(1024), nullable=False), sa.Column("model_version", sa.String(255)), sa.Column("extractor_version", sa.String(255), nullable=False), sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["ocr_result_id"], ["document_ocr_results.id"], ondelete="RESTRICT"), sa.UniqueConstraint("ocr_result_id", "candidate_index", name="uq_document_extracted_fields_result_index"),
    )
    op.create_index("ix_document_extracted_fields_document_name", "document_extracted_fields", ["document_id", "field_name"])
    op.create_table(
        "document_validation_results",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("document_id", sa.Uuid(), nullable=False), sa.Column("ocr_result_id", sa.Uuid(), nullable=False), sa.Column("processing_job_id", sa.Uuid(), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("status", sa.String(32), nullable=False), sa.Column("validation_version", sa.String(255), nullable=False), sa.Column("report_json", postgresql.JSONB(), nullable=False), sa.Column("confidence_summary_json", postgresql.JSONB(), nullable=False), sa.Column("checks_json", postgresql.JSONB(), nullable=False), sa.Column("review_task_id", sa.Uuid()), sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["ocr_result_id"], ["document_ocr_results.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["processing_job_id"], ["processing_jobs.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["review_task_id"], ["review_tasks.id"], ondelete="SET NULL"), sa.UniqueConstraint("document_id", "version", name="uq_document_validation_results_document_version"), sa.UniqueConstraint("processing_job_id", name="uq_document_validation_results_processing_job"), sa.UniqueConstraint("review_task_id", name="uq_document_validation_results_review_task_id"),
    )
    op.create_index("ix_document_validation_results_document_version", "document_validation_results", ["document_id", "version"])
    op.create_table(
        "document_field_corrections",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("document_id", sa.Uuid(), nullable=False), sa.Column("extracted_field_id", sa.Uuid(), nullable=False), sa.Column("version", sa.Integer(), nullable=False), sa.Column("corrected_value", sa.Text(), nullable=False), sa.Column("reason", sa.Text(), nullable=False), sa.Column("created_by_user_id", sa.Uuid()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["extracted_field_id"], ["document_extracted_fields.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"), sa.UniqueConstraint("extracted_field_id", "version", name="uq_document_field_corrections_field_version"),
    )
    op.create_index("ix_document_field_corrections_document_field", "document_field_corrections", ["document_id", "extracted_field_id"])


def downgrade() -> None:
    op.drop_table("document_field_corrections")
    op.drop_table("document_validation_results")
    op.drop_table("document_extracted_fields")
    op.drop_table("document_ocr_results")
    op.drop_table("document_processing_jobs")
    op.drop_table("documents")

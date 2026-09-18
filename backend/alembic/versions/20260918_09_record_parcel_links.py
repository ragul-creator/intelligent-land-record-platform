"""Add Phase G.1 validated record-to-parcel workflow associations.

Revision ID: 20260918_09
Revises: 20260918_08
Create Date: 2026-09-18
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260918_09"
down_revision = "20260918_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_documents_project_id_id", "documents", ["project_id", "id"])
    op.create_unique_constraint("uq_parcels_project_id_id", "parcels", ["project_id", "id"])
    op.create_unique_constraint(
        "uq_document_validation_results_document_id_id",
        "document_validation_results",
        ["document_id", "id"],
    )
    op.create_table(
        "record_parcel_links",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("document_validation_result_id", sa.Uuid(), nullable=False),
        sa.Column("parcel_id", sa.Uuid(), nullable=False),
        sa.Column("link_status", sa.String(length=32), nullable=False),
        sa.Column("link_method", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float()),
        sa.Column("rationale_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("provenance_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Uuid()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)),
        sa.Column("review_task_id", sa.Uuid()),
        sa.Column("review_reason", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("link_status IN ('SUGGESTED', 'REVIEW_REQUIRED', 'CONFIRMED', 'REJECTED')", name="ck_record_parcel_links_status"),
        sa.CheckConstraint("link_method IN ('EXACT_SURVEY_IDENTIFIER', 'ATTRIBUTE_MATCH', 'SPATIAL_CONTEXT', 'MANUAL')", name="ck_record_parcel_links_method"),
        sa.CheckConstraint("confidence IS NULL OR (confidence >= 0 AND confidence <= 1)", name="ck_record_parcel_links_confidence"),
        sa.ForeignKeyConstraint(["project_id", "document_id"], ["documents.project_id", "documents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["project_id", "parcel_id"], ["parcels.project_id", "parcels.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["document_id", "document_validation_result_id"], ["document_validation_results.document_id", "document_validation_results.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reviewed_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["review_task_id"], ["review_tasks.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("review_task_id", name="uq_record_parcel_links_review_task_id"),
    )
    op.create_index("ix_record_parcel_links_project_document", "record_parcel_links", ["project_id", "document_id"])
    op.create_index("ix_record_parcel_links_project_parcel", "record_parcel_links", ["project_id", "parcel_id"])
    op.create_index("uq_record_parcel_links_active_candidate", "record_parcel_links", ["document_validation_result_id", "parcel_id"], unique=True, postgresql_where=sa.text("link_status IN ('SUGGESTED', 'REVIEW_REQUIRED', 'CONFIRMED')"))
    op.create_index("uq_record_parcel_links_confirmed_validation", "record_parcel_links", ["document_validation_result_id"], unique=True, postgresql_where=sa.text("link_status = 'CONFIRMED'"))


def downgrade() -> None:
    op.drop_table("record_parcel_links")
    op.drop_constraint("uq_document_validation_results_document_id_id", "document_validation_results", type_="unique")
    op.drop_constraint("uq_parcels_project_id_id", "parcels", type_="unique")
    op.drop_constraint("uq_documents_project_id_id", "documents", type_="unique")

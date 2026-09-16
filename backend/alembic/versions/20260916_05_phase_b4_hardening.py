"""Persist upload category and constrain processing-job states for Phase B.4.

Revision ID: 20260916_05
Revises: 20260916_04
Create Date: 2026-09-16
"""

from alembic import op
import sqlalchemy as sa


revision = "20260916_05"
down_revision = "20260916_04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("category", sa.String(length=20), server_default="DOCUMENT", nullable=False),
    )
    op.create_check_constraint(
        "ck_files_category",
        "files",
        "category IN ('DOCUMENT', 'IMAGERY', 'GIS', 'SUPPORTING')",
    )
    op.create_check_constraint(
        "ck_processing_jobs_status",
        "processing_jobs",
        "status IN ('QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_processing_jobs_status", "processing_jobs", type_="check")
    op.drop_constraint("ck_files_category", "files", type_="check")
    op.drop_column("files", "category")

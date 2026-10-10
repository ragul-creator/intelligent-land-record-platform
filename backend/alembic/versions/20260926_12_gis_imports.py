"""Add gis_import_runs table for GeoPackage GIS import tracking.

Revision ID: 20260926_12
Revises: 20260926_11
Create Date: 2026-09-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260926_12"
down_revision = "20260926_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gis_import_runs",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid(), nullable=False),
        sa.Column("processing_job_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("source_reference", sa.String(length=1024), nullable=True),
        sa.Column(
            "layer_mapping_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default="QUEUED",
            nullable=False,
        ),
        sa.Column(
            "detected_layers_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "summary_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED', 'RETRY_QUEUED')",
            name="ck_gis_import_runs_status",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["processing_job_id"], ["processing_jobs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("processing_job_id", name="uq_gis_import_runs_processing_job_id"),
    )
    op.create_index(
        "ix_gis_import_runs_project_created",
        "gis_import_runs",
        ["project_id", "created_at"],
    )
    op.create_index("ix_gis_import_runs_file_id", "gis_import_runs", ["file_id"])
    op.create_index("ix_gis_import_runs_status", "gis_import_runs", ["status"])
    op.create_index(
        "ix_gis_import_runs_requested_by",
        "gis_import_runs",
        ["requested_by_user_id"],
    )


def downgrade() -> None:
    op.drop_table("gis_import_runs")

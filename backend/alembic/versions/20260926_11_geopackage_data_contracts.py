"""Allow GIS_IMPORT file category and GEOPACKAGE_IMPORT job type constraints.

Revision ID: 20260926_11
Revises: 20260922_10
Create Date: 2026-09-26
"""

from alembic import op


revision = "20260926_11"
down_revision = "20260922_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_files_category", "files", type_="check")
    op.create_check_constraint(
        "ck_files_category",
        "files",
        "category IN ('DOCUMENT', 'IMAGERY', 'GIS', 'SUPPORTING', 'GIS_IMPORT')",
    )
    op.drop_constraint("ck_geoai_jobs_type", "geoai_jobs", type_="check")
    op.create_check_constraint(
        "ck_geoai_jobs_type",
        "geoai_jobs",
        "job_type IN ('PARCEL_IMPORT', 'BUILDING_VECTORIZE', 'ROAD_VECTORIZE', 'ROAD_IMPORT', 'LAND_USE_IMPORT', 'TOPOLOGY_VALIDATE', 'GEOPACKAGE_IMPORT')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_geoai_jobs_type", "geoai_jobs", type_="check")
    op.create_check_constraint(
        "ck_geoai_jobs_type",
        "geoai_jobs",
        "job_type IN ('PARCEL_IMPORT', 'BUILDING_VECTORIZE', 'ROAD_VECTORIZE', 'ROAD_IMPORT', 'LAND_USE_IMPORT', 'TOPOLOGY_VALIDATE')",
    )
    op.drop_constraint("ck_files_category", "files", type_="check")
    op.create_check_constraint(
        "ck_files_category",
        "files",
        "category IN ('DOCUMENT', 'IMAGERY', 'GIS', 'SUPPORTING')",
    )

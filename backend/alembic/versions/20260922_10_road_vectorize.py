"""Allow the H.2B.5 road-vectorization GeoAI workflow."""

from alembic import op


revision = "20260922_10"
down_revision = "20260918_09"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_geoai_jobs_type", "geoai_jobs", type_="check")
    op.create_check_constraint(
        "ck_geoai_jobs_type",
        "geoai_jobs",
        "job_type IN ('PARCEL_IMPORT', 'BUILDING_VECTORIZE', 'ROAD_VECTORIZE', 'ROAD_IMPORT', 'LAND_USE_IMPORT', 'TOPOLOGY_VALIDATE')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_geoai_jobs_type", "geoai_jobs", type_="check")
    op.create_check_constraint(
        "ck_geoai_jobs_type",
        "geoai_jobs",
        "job_type IN ('PARCEL_IMPORT', 'BUILDING_VECTORIZE', 'ROAD_IMPORT', 'LAND_USE_IMPORT', 'TOPOLOGY_VALIDATE')",
    )

"""Allow AI-assisted parcel delineation GeoAI jobs."""

from alembic import op


revision = "20260928_12"
down_revision = "20260928_11"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_geoai_jobs_type", "geoai_jobs", type_="check")
    op.create_check_constraint(
        "ck_geoai_jobs_type",
        "geoai_jobs",
        "job_type IN ('PARCEL_IMPORT', 'PARCEL_DELINEATE', 'BUILDING_VECTORIZE', 'ROAD_VECTORIZE', 'LAND_USE_VECTORIZE', 'ROAD_IMPORT', 'LAND_USE_IMPORT', 'TOPOLOGY_VALIDATE')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_geoai_jobs_type", "geoai_jobs", type_="check")
    op.create_check_constraint(
        "ck_geoai_jobs_type",
        "geoai_jobs",
        "job_type IN ('PARCEL_IMPORT', 'BUILDING_VECTORIZE', 'ROAD_VECTORIZE', 'LAND_USE_VECTORIZE', 'ROAD_IMPORT', 'LAND_USE_IMPORT', 'TOPOLOGY_VALIDATE')",
    )

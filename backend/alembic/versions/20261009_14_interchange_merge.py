"""Merge GeoPackage interchange with deployed parcel and land-use workflows."""

from alembic import op

revision = "20261009_14"
down_revision = ("20260928_12", "20260926_13")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Both parent branches update the same check. Restore their union regardless
    # of which branch Alembic applied last.
    op.drop_constraint("ck_geoai_jobs_type", "geoai_jobs", type_="check")
    op.create_check_constraint(
        "ck_geoai_jobs_type",
        "geoai_jobs",
        "job_type IN ('PARCEL_IMPORT', 'PARCEL_DELINEATE', 'BUILDING_VECTORIZE', "
        "'ROAD_VECTORIZE', 'LAND_USE_VECTORIZE', 'ROAD_IMPORT', 'LAND_USE_IMPORT', "
        "'TOPOLOGY_VALIDATE', 'GEOPACKAGE_IMPORT')",
    )


def downgrade() -> None:
    # Keep the union while both parent revisions remain applied. Their own
    # downgrades restore the constraints when a parent branch is removed.
    pass

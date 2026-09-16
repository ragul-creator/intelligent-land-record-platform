"""Add Phase C.7 GeoAI/PostGIS persistence and parcel version history.

Revision ID: 20260916_06
Revises: 20260916_05
Create Date: 2026-09-16
"""

from alembic import op
import geoalchemy2
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260916_06"
down_revision = "20260916_05"
branch_labels = None
depends_on = None


def _world_geometry() -> geoalchemy2.Geometry:
    """Use a generic PostGIS geometry type because features may be Polygon or MultiPolygon."""
    return geoalchemy2.Geometry(geometry_type="GEOMETRY", srid=4326, spatial_index=False)


def upgrade() -> None:
    op.drop_constraint("ck_processing_jobs_status", "processing_jobs", type_="check")
    op.create_check_constraint(
        "ck_processing_jobs_status",
        "processing_jobs",
        "status IN ('QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED', 'CANCELLED')",
    )

    op.create_table(
        "imagery_assets",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("file_id", sa.Uuid()),
        sa.Column("source_reference", sa.String(length=1024)),
        sa.Column("source_crs", sa.String(length=255)),
        sa.Column("coordinate_space", sa.String(length=16), server_default="WORLD", nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("file_id"),
    )
    op.create_index("ix_imagery_assets_project_created", "imagery_assets", ["project_id", "created_at"])

    op.create_table(
        "geoai_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("imagery_asset_id", sa.Uuid()),
        sa.Column("requested_by_user_id", sa.Uuid()),
        sa.Column("job_type", sa.String(length=50), nullable=False),
        sa.Column("parameters_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("metrics_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("output_refs_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "job_type IN ('PARCEL_IMPORT', 'BUILDING_VECTORIZE', 'ROAD_IMPORT', 'LAND_USE_IMPORT', 'TOPOLOGY_VALIDATE')",
            name="ck_geoai_jobs_type",
        ),
        sa.ForeignKeyConstraint(["id"], ["processing_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["imagery_asset_id"], ["imagery_assets.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_geoai_jobs_project_created", "geoai_jobs", ["project_id", "created_at"])

    op.create_table(
        "parcels",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("external_identifier", sa.String(length=255)),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_reference", sa.String(length=1024)),
        sa.Column("status", sa.String(length=32), server_default="DRAFT", nullable=False),
        sa.Column("verification_status", sa.String(length=32), server_default="UNVERIFIED", nullable=False),
        sa.Column("current_geometry_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("coordinate_space", sa.String(length=16), nullable=False),
        sa.Column("source_crs", sa.String(length=255)),
        sa.Column("confidence", sa.Float()),
        sa.Column("model_version", sa.String(length=255)),
        sa.Column("ai_boundary_status", sa.String(length=32)),
        sa.Column("requires_survey", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("current_geometry_version >= 1", name="ck_parcels_current_geometry_version"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
    )
    op.create_index("ix_parcels_project_status", "parcels", ["project_id", "status"])
    op.create_index("ix_parcels_project_external_identifier", "parcels", ["project_id", "external_identifier"])

    op.create_table(
        "parcel_geometry_versions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("parcel_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("geometry", _world_geometry()),
        sa.Column("source_geometry_json", postgresql.JSONB()),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("source_reference", sa.String(length=1024)),
        sa.Column("coordinate_space", sa.String(length=16), nullable=False),
        sa.Column("source_crs", sa.String(length=255)),
        sa.Column("area_m2", sa.Float()),
        sa.Column("area_sqft", sa.Float()),
        sa.Column("change_reason", sa.Text()),
        sa.Column("validation_status", sa.String(length=32)),
        sa.Column("created_by_user_id", sa.Uuid()),
        sa.Column("created_by_type", sa.String(length=16), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["parcel_id"], ["parcels.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("parcel_id", "version", name="uq_parcel_geometry_versions_parcel_version"),
    )
    op.create_index("ix_parcel_geometry_versions_parcel_version", "parcel_geometry_versions", ["parcel_id", "version"])
    op.create_index("ix_parcel_geometry_versions_geometry", "parcel_geometry_versions", ["geometry"], postgresql_using="gist")

    for table_name, class_column, measurement_column in (
        ("buildings", None, None),
        ("roads", "road_class", "length_m"),
        ("land_use_features", "land_use_class", "area_m2"),
    ):
        columns = [
            sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
            sa.Column("project_id", sa.Uuid(), nullable=False),
            sa.Column("geometry", _world_geometry(), nullable=False),
        ]
        if class_column is not None:
            columns.append(sa.Column(class_column, sa.String(length=32), nullable=False))
        columns.extend(
            [
                sa.Column("source", sa.String(length=50), nullable=False),
                sa.Column("source_reference", sa.String(length=1024)),
                sa.Column("confidence", sa.Float()),
                sa.Column("model_version", sa.String(length=255)),
                sa.Column("status", sa.String(length=32), nullable=False),
                sa.Column("verification_status", sa.String(length=32), nullable=False),
            ]
        )
        if table_name != "roads":
            columns.extend([sa.Column("area_m2", sa.Float()), sa.Column("area_sqft", sa.Float())])
        else:
            columns.append(sa.Column(measurement_column, sa.Float()))
        columns.extend(
            [
                sa.Column("processed_at", sa.DateTime(timezone=True)),
                sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
            ]
        )
        op.create_table(table_name, *columns)
        op.create_index(f"ix_{table_name}_geometry", table_name, ["geometry"], postgresql_using="gist")
    op.create_index("ix_buildings_project_status", "buildings", ["project_id", "status"])
    op.create_index("ix_roads_project_class", "roads", ["project_id", "road_class"])
    op.create_index("ix_land_use_features_project_class", "land_use_features", ["project_id", "land_use_class"])

    op.create_table(
        "topology_errors",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("parcel_id", sa.Uuid()),
        sa.Column("related_parcel_id", sa.Uuid()),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("severity", sa.String(length=32), nullable=False),
        sa.Column("area_m2", sa.Float()),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("resolved", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by_user_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parcel_id"], ["parcels.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["related_parcel_id"], ["parcels.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_topology_errors_project_resolved", "topology_errors", ["project_id", "resolved"])


def downgrade() -> None:
    op.drop_table("topology_errors")
    for table_name in ("land_use_features", "roads", "buildings", "parcel_geometry_versions", "parcels", "geoai_jobs", "imagery_assets"):
        op.drop_table(table_name)
    op.drop_constraint("ck_processing_jobs_status", "processing_jobs", type_="check")
    op.create_check_constraint(
        "ck_processing_jobs_status",
        "processing_jobs",
        "status IN ('QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED')",
    )

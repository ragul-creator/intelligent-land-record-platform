"""Phase B.1 relational foundation for platform persistence."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BIGINT,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from geoalchemy2 import Geometry

from app.core.database import Base


class TimestampedModel:
    """UTC timestamps managed by PostgreSQL for mutable business entities."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class User(TimestampedModel, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "login_id ~ '^(ADM|OFF|REV|SUR|VWR)-TN-[0-9]{6,}$'",
            name="ck_users_login_id_format",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    login_id: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true", nullable=False)


class Role(TimestampedModel, Base):
    __tablename__ = "roles"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class Permission(TimestampedModel, Base):
    __tablename__ = "permissions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="RESTRICT"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class RolePermission(Base):
    __tablename__ = "role_permissions"

    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True
    )
    permission_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("permissions.id", ondelete="CASCADE"), primary_key=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index("ix_auth_sessions_user_active", "user_id", "revoked_at"),
        Index("ix_auth_sessions_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Project(TimestampedModel, Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("owner_id", "name", name="uq_projects_owner_name"),
        CheckConstraint("state IN ('ACTIVE', 'ARCHIVED')", name="ck_projects_state"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(String(20), server_default="ACTIVE", nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT"), index=True, nullable=False
    )


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (Index("ix_project_members_user_project", "user_id", "project_id"),)

    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(
        String(50), ForeignKey("roles.name", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class File(TimestampedModel, Base):
    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_files_size_bytes_nonnegative"),
        CheckConstraint("char_length(sha256) = 64", name="ck_files_sha256_length"),
        CheckConstraint(
            "category IN ('DOCUMENT', 'IMAGERY', 'GIS', 'SUPPORTING', 'GIS_IMPORT')",
            name="ck_files_category",
        ),
        Index("ix_files_project_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    category: Mapped[str] = mapped_column(String(20), default="DOCUMENT", server_default="DOCUMENT", nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BIGINT, nullable=False)
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    storage_key: Mapped[str] = mapped_column(String(1024), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), server_default="UPLOADED", nullable=False)


class ProcessingJob(TimestampedModel, Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key", name="uq_processing_jobs_project_key"),
        CheckConstraint("progress >= 0 AND progress <= 100", name="ck_processing_jobs_progress"),
        CheckConstraint("retry_count >= 0", name="ck_processing_jobs_retry_count"),
        CheckConstraint(
            "status IN ('QUEUED', 'PROCESSING', 'COMPLETED', 'FAILED', 'CANCELLED')",
            name="ck_processing_jobs_status",
        ),
        Index("ix_processing_jobs_project_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    job_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(32), server_default="QUEUED", nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    progress: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, server_default="0", nullable=False)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class ImageryAsset(TimestampedModel, Base):
    """Project imagery metadata for later GeoAI input and layer association."""

    __tablename__ = "imagery_assets"
    __table_args__ = (Index("ix_imagery_assets_project_created", "project_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    file_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("files.id", ondelete="SET NULL"), unique=True)
    source_reference: Mapped[str | None] = mapped_column(String(1024))
    source_crs: Mapped[str | None] = mapped_column(String(255))
    coordinate_space: Mapped[str] = mapped_column(String(16), server_default="WORLD", nullable=False)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", nullable=False)


class GeoAIJob(TimestampedModel, Base):
    """GeoAI-specific request/provenance linked to the common job lifecycle."""

    __tablename__ = "geoai_jobs"
    __table_args__ = (
        CheckConstraint(
            "job_type IN ('PARCEL_IMPORT', 'BUILDING_VECTORIZE', 'ROAD_VECTORIZE', 'ROAD_IMPORT', 'LAND_USE_IMPORT', 'TOPOLOGY_VALIDATE', 'GEOPACKAGE_IMPORT')",
            name="ck_geoai_jobs_type",
        ),
        Index("ix_geoai_jobs_project_created", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("processing_jobs.id", ondelete="CASCADE"), primary_key=True)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    imagery_asset_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("imagery_assets.id", ondelete="SET NULL"))
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    parameters_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", nullable=False)
    output_refs_json: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}", nullable=False)


class Parcel(TimestampedModel, Base):
    """Draft parcel identity with its current immutable geometry-version pointer."""

    __tablename__ = "parcels"
    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_parcels_project_id_id"),
        CheckConstraint("current_geometry_version >= 1", name="ck_parcels_current_geometry_version"),
        Index("ix_parcels_project_status", "project_id", "status"),
        Index("ix_parcels_project_external_identifier", "project_id", "external_identifier"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    external_identifier: Mapped[str | None] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(32), server_default="DRAFT", nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), server_default="UNVERIFIED", nullable=False)
    current_geometry_version: Mapped[int] = mapped_column(Integer, server_default="1", nullable=False)
    coordinate_space: Mapped[str] = mapped_column(String(16), nullable=False)
    source_crs: Mapped[str | None] = mapped_column(String(255))
    confidence: Mapped[float | None] = mapped_column()
    model_version: Mapped[str | None] = mapped_column(String(255))
    ai_boundary_status: Mapped[str | None] = mapped_column(String(32))
    requires_survey: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)


class ParcelGeometryVersion(Base):
    """Append-only parcel geometry/provenance record; historical versions are never updated."""

    __tablename__ = "parcel_geometry_versions"
    __table_args__ = (
        UniqueConstraint("parcel_id", "version", name="uq_parcel_geometry_versions_parcel_version"),
        Index("ix_parcel_geometry_versions_parcel_version", "parcel_id", "version"),
        Index("ix_parcel_geometry_versions_geometry", "geometry", postgresql_using="gist"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    parcel_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("parcels.id", ondelete="RESTRICT"), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    geometry: Mapped[Any | None] = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=False))
    source_geometry_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(1024))
    coordinate_space: Mapped[str] = mapped_column(String(16), nullable=False)
    source_crs: Mapped[str | None] = mapped_column(String(255))
    area_m2: Mapped[float | None] = mapped_column()
    area_sqft: Mapped[float | None] = mapped_column()
    change_reason: Mapped[str | None] = mapped_column(Text)
    validation_status: Mapped[str | None] = mapped_column(String(32))
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    created_by_type: Mapped[str] = mapped_column(String(16), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Building(TimestampedModel, Base):
    __tablename__ = "buildings"
    __table_args__ = (
        Index("ix_buildings_project_status", "project_id", "status"),
        Index("ix_buildings_geometry", "geometry", postgresql_using="gist"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    geometry: Mapped[Any] = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(1024))
    confidence: Mapped[float | None] = mapped_column()
    model_version: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    area_m2: Mapped[float | None] = mapped_column()
    area_sqft: Mapped[float | None] = mapped_column()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Road(TimestampedModel, Base):
    __tablename__ = "roads"
    __table_args__ = (
        Index("ix_roads_project_class", "project_id", "road_class"),
        Index("ix_roads_geometry", "geometry", postgresql_using="gist"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    geometry: Mapped[Any] = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=False)
    road_class: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(1024))
    confidence: Mapped[float | None] = mapped_column()
    model_version: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    length_m: Mapped[float | None] = mapped_column()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LandUseFeature(TimestampedModel, Base):
    __tablename__ = "land_use_features"
    __table_args__ = (
        Index("ix_land_use_features_project_class", "project_id", "land_use_class"),
        Index("ix_land_use_features_geometry", "geometry", postgresql_using="gist"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    geometry: Mapped[Any] = mapped_column(Geometry("GEOMETRY", srid=4326, spatial_index=False), nullable=False)
    land_use_class: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(1024))
    confidence: Mapped[float | None] = mapped_column()
    model_version: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    verification_status: Mapped[str] = mapped_column(String(32), nullable=False)
    area_m2: Mapped[float | None] = mapped_column()
    area_sqft: Mapped[float | None] = mapped_column()
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TopologyError(Base):
    __tablename__ = "topology_errors"
    __table_args__ = (Index("ix_topology_errors_project_resolved", "project_id", "resolved"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False)
    parcel_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("parcels.id", ondelete="SET NULL"))
    related_parcel_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("parcels.id", ondelete="SET NULL"))
    code: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(32), nullable=False)
    area_m2: Mapped[float | None] = mapped_column()
    message: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(Boolean, server_default="false", nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_project_created", "project_id", "created_at"),
        Index("ix_audit_logs_actor_created", "actor_id", "created_at"),
        Index("ix_audit_logs_target", "target_type", "target_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="SET NULL")
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    before_hash: Mapped[str | None] = mapped_column(String(64))
    after_hash: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

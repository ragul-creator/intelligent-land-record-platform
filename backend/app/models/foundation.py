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
        Index("ix_files_project_status", "project_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="RESTRICT"), nullable=False
    )
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
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

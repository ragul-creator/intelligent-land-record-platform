"""Add deterministic RBAC mappings and revocable refresh-token sessions.

Revision ID: 20260916_03
Revises: 20260915_02
Create Date: 2026-09-16
"""

from uuid import NAMESPACE_URL, uuid5

from alembic import op
import sqlalchemy as sa

revision = "20260916_03"
down_revision = "20260915_02"
branch_labels = None
depends_on = None

PERMISSION_CODES = (
    "project:read", "project:create", "project:update", "project:member_manage",
    "document:upload", "document:read", "document:process", "document:reprocess",
    "field:read", "field:correct", "record:read", "record:publish",
    "validation:run", "validation:resolve", "review:read", "review:act",
    "imagery:upload", "geoai:process", "geo:read", "geo:edit_draft", "geo:approve",
    "export:read", "dashboard:read", "audit:read", "user:manage", "role:read",
    "role:manage", "system:admin",
)
ROLE_PERMISSION_CODES = {
    "ADMIN": PERMISSION_CODES,
    "OFFICER": (
        "project:read", "project:create", "project:update", "document:upload", "document:read",
        "document:process", "document:reprocess", "field:read", "field:correct", "record:read",
        "validation:run", "validation:resolve", "review:read", "dashboard:read", "audit:read",
        "export:read", "geo:read", "imagery:upload",
    ),
    "REVIEWER": (
        "project:read", "document:read", "field:read", "field:correct", "record:read",
        "validation:run", "validation:resolve", "review:read", "review:act", "geo:read",
        "geo:approve", "dashboard:read", "export:read",
    ),
    "SURVEYOR": (
        "project:read", "document:read", "record:read", "imagery:upload", "geoai:process",
        "geo:read", "geo:edit_draft", "dashboard:read", "export:read",
    ),
    "VIEWER": (
        "project:read", "document:read", "record:read", "geo:read", "dashboard:read", "export:read",
    ),
}


def _seed_id(kind: str, value: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"land-record-platform/{kind}/{value}"))


def upgrade() -> None:
    op.create_table(
        "role_permissions",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["permission_id"], ["permissions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role_id"], ["roles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("role_id", "permission_id"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_digest", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("token_digest"),
    )
    op.create_index("ix_auth_sessions_user_active", "auth_sessions", ["user_id", "revoked_at"])
    op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    mappings = sa.table(
        "role_permissions",
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
    )
    op.bulk_insert(
        mappings,
        [
            {"role_id": _seed_id("role", role), "permission_id": _seed_id("permission", permission)}
            for role, permissions in ROLE_PERMISSION_CODES.items()
            for permission in permissions
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_auth_sessions_expires_at", table_name="auth_sessions")
    op.drop_index("ix_auth_sessions_user_active", table_name="auth_sessions")
    op.drop_table("auth_sessions")
    op.drop_table("role_permissions")

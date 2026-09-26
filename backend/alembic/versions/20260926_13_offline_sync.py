"""Add sync_operations and sync_changes tables for offline-first sync.

Revision ID: 20260926_13
Revises: 20260926_12
Create Date: 2026-09-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260926_13"
down_revision = "20260926_12"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_operations",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("client_id", sa.String(length=128), nullable=True),
        sa.Column("operation_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column(
            "payload_json",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("result_json", postgresql.JSONB(), nullable=True),
        sa.Column("conflict_json", postgresql.JSONB(), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("client_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "operation_type IN ('PARCEL_VERSION_CREATE', 'FIELD_CORRECTION_CREATE', 'REVIEW_TASK_UPDATE', 'RECORD_LINK_RESOLVE')",
            name="ck_sync_operations_type",
        ),
        sa.CheckConstraint(
            "status IN ('APPLIED', 'DUPLICATE', 'CONFLICT', 'REJECTED')",
            name="ck_sync_operations_status",
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index(
        "ix_sync_operations_project_created",
        "sync_operations",
        ["project_id", "created_at"],
    )
    op.create_index(
        "ix_sync_operations_project_entity",
        "sync_operations",
        ["project_id", "entity_id"],
    )
    op.create_index("ix_sync_operations_user_id", "sync_operations", ["user_id"])
    op.create_index("ix_sync_operations_client_id", "sync_operations", ["client_id"])

    op.create_table(
        "sync_changes",
        sa.Column(
            "id",
            sa.BIGINT(),
            sa.Identity(start=1, increment=1),
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("change_type", sa.String(length=32), nullable=False),
        sa.Column("server_version", sa.Integer(), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
    )
    op.create_index(
        "ix_sync_changes_project_cursor",
        "sync_changes",
        ["project_id", "id"],
    )
    op.create_index(
        "ix_sync_changes_entity",
        "sync_changes",
        ["entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_table("sync_changes")
    op.drop_table("sync_operations")

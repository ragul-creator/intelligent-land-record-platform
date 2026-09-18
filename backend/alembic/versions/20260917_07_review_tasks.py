"""Add Phase E.2 human review queue persistence.

Revision ID: 20260917_07
Revises: 20260916_06
Create Date: 2026-09-17
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "20260917_07"
down_revision = "20260916_06"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("queue_type", sa.String(length=16), nullable=False),
        sa.Column("target_type", sa.String(length=64), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="OPEN", nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("source_refs_json", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("blocking_issue_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("assignee_user_id", sa.Uuid()),
        sa.Column("created_by_user_id", sa.Uuid()),
        sa.Column("escalated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("resolution_action", sa.String(length=32)),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("resolved_by_user_id", sa.Uuid()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("queue_type IN ('DOCUMENT', 'GIS')", name="ck_review_tasks_queue_type"),
        sa.CheckConstraint("severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH')", name="ck_review_tasks_severity"),
        sa.CheckConstraint("status IN ('OPEN', 'RESOLVED')", name="ck_review_tasks_status"),
        sa.CheckConstraint("blocking_issue_count >= 0", name="ck_review_tasks_blocking_issue_count"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_review_tasks_project_status", "review_tasks", ["project_id", "status"])
    op.create_index(
        "ix_review_tasks_project_queue_status",
        "review_tasks",
        ["project_id", "queue_type", "status"],
    )
    op.create_index("ix_review_tasks_assignee_status", "review_tasks", ["assignee_user_id", "status"])
    op.create_index("ix_review_tasks_target", "review_tasks", ["target_type", "target_id"])


def downgrade() -> None:
    op.drop_index("ix_review_tasks_target", table_name="review_tasks")
    op.drop_index("ix_review_tasks_assignee_status", table_name="review_tasks")
    op.drop_index("ix_review_tasks_project_queue_status", table_name="review_tasks")
    op.drop_index("ix_review_tasks_project_status", table_name="review_tasks")
    op.drop_table("review_tasks")

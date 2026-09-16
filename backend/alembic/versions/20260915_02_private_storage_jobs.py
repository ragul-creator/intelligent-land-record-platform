"""Allow optional file checksums for the private upload registration flow.

Revision ID: 20260915_02
Revises: 20260915_01
Create Date: 2026-09-15
"""

from alembic import op
import sqlalchemy as sa

revision = "20260915_02"
down_revision = "20260915_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("files", "sha256", existing_type=sa.String(length=64), nullable=True)


def downgrade() -> None:
    op.execute("UPDATE files SET sha256 = repeat('0', 64) WHERE sha256 IS NULL")
    op.alter_column("files", "sha256", existing_type=sa.String(length=64), nullable=False)

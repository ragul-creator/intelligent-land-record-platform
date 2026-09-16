"""Add immutable, role-prefixed human-facing login identifiers.

Revision ID: 20260916_04
Revises: 20260916_03
Create Date: 2026-09-16
"""

from alembic import op
import sqlalchemy as sa


revision = "20260916_04"
down_revision = "20260916_03"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("login_id", sa.String(length=32), nullable=True))
    op.execute("CREATE SEQUENCE users_login_id_seq START WITH 1 INCREMENT BY 1")
    op.execute(
        """
        UPDATE users AS u
        SET login_id = COALESCE(
            (
                SELECT CASE r.name
                    WHEN 'ADMIN' THEN 'ADM'
                    WHEN 'OFFICER' THEN 'OFF'
                    WHEN 'REVIEWER' THEN 'REV'
                    WHEN 'SURVEYOR' THEN 'SUR'
                    WHEN 'VIEWER' THEN 'VWR'
                END
                FROM user_roles AS ur
                JOIN roles AS r ON r.id = ur.role_id
                WHERE ur.user_id = u.id
                ORDER BY CASE r.name
                    WHEN 'ADMIN' THEN 1
                    WHEN 'OFFICER' THEN 2
                    WHEN 'REVIEWER' THEN 3
                    WHEN 'SURVEYOR' THEN 4
                    WHEN 'VIEWER' THEN 5
                    ELSE 6
                END
                LIMIT 1
            ),
            'VWR'
        ) || '-TN-' || lpad(nextval('users_login_id_seq')::text, 6, '0')
        WHERE login_id IS NULL
        """
    )
    op.alter_column("users", "login_id", nullable=False)
    op.create_check_constraint(
        "ck_users_login_id_format",
        "users",
        "login_id ~ '^(ADM|OFF|REV|SUR|VWR)-TN-[0-9]{6,}$'",
    )
    op.create_index("ix_users_login_id", "users", ["login_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_login_id", table_name="users")
    op.drop_constraint("ck_users_login_id_format", "users", type_="check")
    op.drop_column("users", "login_id")
    op.execute("DROP SEQUENCE users_login_id_seq")

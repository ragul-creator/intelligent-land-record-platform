"""Create one initial ADMIN from environment values for local development."""

import sys

from sqlalchemy import select

from app.core.auth import hash_password
from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models import Role, User, UserRole
from app.services.user_identities import generate_login_id, validate_login_id_for_role


def resolve_bootstrap_login_id(session, configured_login_id: str | None) -> str:  # noqa: ANN001
    """Use a validated override or allocate the initial ADMIN identifier."""
    if configured_login_id:
        return validate_login_id_for_role(configured_login_id, "ADMIN")
    return generate_login_id(session, "ADMIN")


def main() -> int:
    settings = get_settings()
    if not settings.bootstrap_admin_email or not settings.bootstrap_admin_password:
        print("Set BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD before running this command.")
        return 1
    if len(settings.bootstrap_admin_password) < 12:
        print("BOOTSTRAP_ADMIN_PASSWORD must be at least 12 characters.")
        return 1

    with SessionLocal() as session:
        email = settings.bootstrap_admin_email.strip().lower()
        if session.scalar(select(User).where(User.email == email)) is not None:
            print("Bootstrap administrator already exists; no change was made.")
            return 1
        admin_role = session.scalar(select(Role).where(Role.name == "ADMIN"))
        if admin_role is None:
            print("ADMIN role is missing; apply migrations first.")
            return 1
        try:
            login_id = resolve_bootstrap_login_id(session, settings.bootstrap_admin_login_id)
        except ValueError as error:
            print(str(error))
            return 1
        if session.scalar(select(User).where(User.login_id == login_id)) is not None:
            print("Bootstrap administrator login ID already exists; no change was made.")
            return 1
        user = User(
            login_id=login_id,
            email=email,
            password_hash=hash_password(settings.bootstrap_admin_password),
            full_name=settings.bootstrap_admin_full_name,
            is_active=True,
        )
        session.add(user)
        session.flush()
        session.add(UserRole(user_id=user.id, role_id=admin_role.id))
        session.commit()
    print(f"Bootstrap administrator created with login ID: {login_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

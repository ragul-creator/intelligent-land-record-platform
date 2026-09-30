"""Create the idempotent Phase H.2 synthetic Tamil Nadu demo dataset."""

import os
import sys

from sqlalchemy import select

from app.core.auth import verify_password
from app.core.database import SessionLocal
from app.demo_seed import seed_tamil_nadu_demo
from app.models import User


def main() -> int:
    password = os.getenv("DEMO_SEED_PASSWORD", "")
    if len(password) < 12:
        print("Set DEMO_SEED_PASSWORD to a local demo password of at least 12 characters.")
        return 1

    try:
        with SessionLocal() as session:
            result = seed_tamil_nadu_demo(session, password)
            viewer = session.scalar(select(User).where(User.email == "viewer.demo.tn@example.invalid"))
            if viewer is None or not viewer.is_active or not verify_password(password, viewer.password_hash):
                raise RuntimeError("Demo viewer credential verification failed after seeding.")
    except Exception as error:
        print(f"Demo seed failed: {error}")
        return 1

    print("Tamil Nadu synthetic demo dataset is ready.")
    print("Demo viewer credential verified.")
    print(f"Project ID: {result['project_id']}")
    print("Demo login IDs:")
    for role, login_id in result["users"].items():
        print(f"  {role}: {login_id}")
    print("Use the DEMO_SEED_PASSWORD value you supplied. No password is printed or persisted in logs.")
    print("All seeded records/geometries are synthetic hackathon data, not official land records.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

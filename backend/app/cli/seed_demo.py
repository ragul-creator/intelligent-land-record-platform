"""Create the idempotent Phase H.2 synthetic Tamil Nadu demo dataset."""

import os
import sys

from app.core.database import SessionLocal
from app.demo_seed import seed_tamil_nadu_demo


def main() -> int:
    password = os.getenv("DEMO_SEED_PASSWORD", "")
    if len(password) < 12:
        print("Set DEMO_SEED_PASSWORD to a local demo password of at least 12 characters.")
        return 1

    try:
        with SessionLocal() as session:
            result = seed_tamil_nadu_demo(session, password)
    except Exception as error:
        print(f"Demo seed failed: {error}")
        return 1

    print("Tamil Nadu synthetic demo dataset is ready.")
    print(f"Project ID: {result['project_id']}")
    print("Demo login IDs:")
    for role, login_id in result["users"].items():
        print(f"  {role}: {login_id}")
    print("Use the DEMO_SEED_PASSWORD value you supplied. No password is printed or persisted in logs.")
    print("All seeded records/geometries are synthetic hackathon data, not official land records.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Phase H.1 live RBAC regression matrix across representative project APIs."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth import hash_password
from app.core.database import SessionLocal
from app.main import app
from app.models import Project, ProjectMember, Role, User, UserRole
from app.services.user_identities import generate_login_id


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1",
    reason="Set RUN_DATABASE_TESTS=1 after applying migrations to run H.1 RBAC integration tests.",
)


def _user(session, role_name: str) -> User:
    user = User(
        login_id=generate_login_id(session, role_name),
        email=f"h1-{role_name.lower()}-{uuid.uuid4().hex}@example.invalid",
        password_hash=hash_password("h1-test-password"),
        full_name=f"H1 {role_name}",
    )
    session.add(user)
    session.flush()
    role = session.scalar(select(Role).where(Role.name == role_name))
    assert role is not None
    session.add(UserRole(user_id=user.id, role_id=role.id))
    return user


def _headers(client: TestClient, login_id: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": login_id, "password": "h1-test-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_h1_project_api_role_matrix_blocks_privilege_drift() -> None:
    with SessionLocal() as session:
        users = {role: _user(session, role) for role in ("ADMIN", "OFFICER", "REVIEWER", "SURVEYOR", "VIEWER")}
        project = Project(name=f"H1 RBAC {uuid.uuid4().hex}", owner_id=users["ADMIN"].id, state="ACTIVE")
        session.add(project)
        session.flush()
        for role, user in users.items():
            session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
        session.commit()
        project_id = project.id
        login_ids = {role: user.login_id for role, user in users.items()}

    client = TestClient(app)
    headers = {role: _headers(client, login_id) for role, login_id in login_ids.items()}

    # Every approved role has dashboard:read, but that does not grant write access.
    for role in headers:
        response = client.get(f"/api/v1/projects/{project_id}/dashboard", headers=headers[role])
        assert response.status_code == 200
        assert response.json()["project_role"] == role

    for role in ("ADMIN", "OFFICER"):
        response = client.patch(
            f"/api/v1/projects/{project_id}",
            json={"description": f"Updated by {role}"},
            headers=headers[role],
        )
        assert response.status_code == 200
    for role in ("REVIEWER", "SURVEYOR", "VIEWER"):
        assert client.patch(
            f"/api/v1/projects/{project_id}",
            json={"description": "Blocked write"},
            headers=headers[role],
        ).status_code == 403

    for role in ("ADMIN", "OFFICER", "REVIEWER"):
        assert client.get(
            f"/api/v1/review/tasks?project_id={project_id}",
            headers=headers[role],
        ).status_code == 200
    for role in ("SURVEYOR", "VIEWER"):
        assert client.get(
            f"/api/v1/review/tasks?project_id={project_id}",
            headers=headers[role],
        ).status_code == 403

    for role in ("ADMIN", "OFFICER"):
        assert client.get(f"/api/v1/projects/{project_id}/audit", headers=headers[role]).status_code == 200
    for role in ("REVIEWER", "SURVEYOR", "VIEWER"):
        assert client.get(f"/api/v1/projects/{project_id}/audit", headers=headers[role]).status_code == 403

    # Project membership alone never gives member-management authority.
    for role in ("OFFICER", "REVIEWER", "SURVEYOR", "VIEWER"):
        response = client.post(
            f"/api/v1/projects/{project_id}/members",
            json={"user_id": str(users["VIEWER"].id), "role": "VIEWER"},
            headers=headers[role],
        )
        assert response.status_code == 403

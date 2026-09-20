import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.auth import hash_password, user_permissions
from app.core.database import SessionLocal
from app.core.permissions import ROLE_PERMISSION_CODES
from app.main import app
from app.models import AuditLog, AuthSession, File, Project, ProjectMember, Role, User, UserRole
from app.services.user_identities import generate_login_id

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1",
    reason="Set RUN_DATABASE_TESTS=1 after applying migrations to run auth integration tests.",
)


def create_user(session, suffix: str, role_name: str, active: bool = True) -> User:
    user = User(
        login_id=generate_login_id(session, role_name),
        email=f"phase-b3-{suffix}-{uuid.uuid4().hex}@example.invalid",
        password_hash=hash_password("phase-b3-test-password"),
        full_name=f"Phase B3 {role_name}",
        is_active=active,
    )
    session.add(user)
    session.flush()
    role = session.scalar(select(Role).where(Role.name == role_name))
    assert role is not None
    session.add(UserRole(user_id=user.id, role_id=role.id))
    return user


def create_project(session, owner: User, name: str) -> Project:
    project = Project(name=f"{name} {uuid.uuid4().hex}", owner_id=owner.id, state="ACTIVE")
    session.add(project)
    session.flush()
    return project


def login(client: TestClient, identifier: str, password: str = "phase-b3-test-password") -> dict:
    response = client.post("/api/v1/auth/login", json={"identifier": identifier, "password": password})
    assert response.status_code == 200
    return response.json()


def test_authentication_refresh_rotation_logout_and_inactive_denial() -> None:
    with SessionLocal() as session:
        officer = create_user(session, "auth", "OFFICER")
        inactive = create_user(session, "inactive", "VIEWER", active=False)
        session.commit()
        officer_email = officer.email
        officer_login_id = officer.login_id
        inactive_email = inactive.email
        inactive_login_id = inactive.login_id

    client = TestClient(app)
    tokens = login(client, officer_login_id)
    with SessionLocal() as session:
        stored_digests = list(session.scalars(select(AuthSession.token_digest)))
        assert tokens["refresh_token"] not in stored_digests
        assert all(len(digest) == 64 for digest in stored_digests)
    me = client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert me.status_code == 200
    assert me.json()["login_id"] == officer_login_id
    assert me.json()["roles"] == ["OFFICER"]
    assert "document:upload" in me.json()["permissions"]

    # The legacy email field remains accepted while clients migrate to identifier.
    assert client.post("/api/v1/auth/login", json={"email": officer_email, "password": "phase-b3-test-password"}).status_code == 200
    wrong_password = client.post(
        "/api/v1/auth/login", json={"identifier": officer_login_id, "password": "wrong"}
    )
    unknown_user = client.post(
        "/api/v1/auth/login", json={"identifier": "VWR-TN-999999", "password": "wrong"}
    )
    inactive_user = client.post(
        "/api/v1/auth/login",
        json={"identifier": inactive_login_id, "password": "phase-b3-test-password"},
    )
    assert {response.status_code for response in (wrong_password, unknown_user, inactive_user)} == {401}
    assert {response.json()["error"]["code"] for response in (wrong_password, unknown_user, inactive_user)} == {"AUTHENTICATION_FAILED"}
    assert {response.json()["error"]["message"] for response in (wrong_password, unknown_user, inactive_user)} == {"Invalid credentials."}

    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != tokens["refresh_token"]
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}).status_code == 401
    assert client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {rotated.json()['refresh_token']}"}).status_code == 401
    assert client.post("/api/v1/auth/logout", json={"refresh_token": rotated.json()["refresh_token"]}).status_code == 204
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": rotated.json()["refresh_token"]}).status_code == 401
    assert client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {rotated.json()['access_token']}"},
    ).status_code == 401
    with SessionLocal() as session:
        actions = set(session.scalars(select(AuditLog.action)))
        assert {"auth.login_success", "auth.login_failure", "auth.refresh_rotated", "auth.logout"} <= actions
        officer = session.scalar(select(User).where(User.email == officer_email))
        assert officer is not None
        officer.is_active = False
        session.commit()
    assert client.get("/api/v1/users/me", headers={"Authorization": f"Bearer {rotated.json()['access_token']}"}).status_code == 401


def test_role_permissions_project_scope_and_file_idor_protection() -> None:
    with SessionLocal() as session:
        officer = create_user(session, "officer", "OFFICER")
        admin = create_user(session, "admin", "ADMIN")
        owner = create_user(session, "owner", "VIEWER")
        project_a = create_project(session, owner, "Project A")
        project_b = create_project(session, owner, "Project B")
        session.add(ProjectMember(project_id=project_a.id, user_id=officer.id, role="OFFICER"))
        session.add(
            File(
                project_id=project_b.id,
                original_name="other-project.pdf",
                mime_type="application/pdf",
                size_bytes=1,
                storage_key=f"projects/{project_b.id}/originals/{uuid.uuid4()}/other-project.pdf",
                status="UPLOADED",
            )
        )
        session.add(
            File(
                project_id=project_b.id,
                original_name="pending-other-project.pdf",
                mime_type="application/pdf",
                size_bytes=1,
                storage_key=f"projects/{project_b.id}/originals/{uuid.uuid4()}/pending-other-project.pdf",
                status="PENDING_UPLOAD",
            )
        )
        session.flush()
        foreign_file = session.scalar(select(File).where(File.project_id == project_b.id))
        assert foreign_file is not None
        pending_foreign_file = session.scalar(
            select(File).where(File.project_id == project_b.id, File.status == "PENDING_UPLOAD")
        )
        assert pending_foreign_file is not None
        assert user_permissions(session, officer.id) == set(ROLE_PERMISSION_CODES["OFFICER"])
        assert user_permissions(session, admin.id) == set(ROLE_PERMISSION_CODES["ADMIN"])
        session.commit()
        officer_email = officer.email
        officer_login_id = officer.login_id
        admin_email = admin.email
        project_a_id = project_a.id
        project_b_id = project_b.id
        foreign_file_id = foreign_file.id
        pending_foreign_file_id = pending_foreign_file.id

    client = TestClient(app)
    officer_tokens = login(client, officer_login_id)
    headers = {"Authorization": f"Bearer {officer_tokens['access_token']}"}
    payload = {
        "filename": "authorized.pdf",
        "content_type": "application/pdf",
        "size_bytes": 1,
        "category": "DOCUMENT",
    }
    assert client.post("/api/v1/files/presign", json={**payload, "project_id": str(project_a_id)}, headers=headers).status_code == 201
    assert client.post("/api/v1/files/presign", json={**payload, "project_id": str(project_b_id)}, headers=headers).status_code == 404
    assert client.get(f"/api/v1/files/{foreign_file_id}/download", headers=headers).status_code == 404
    assert client.post("/api/v1/files/complete", json={"file_id": str(pending_foreign_file_id)}, headers=headers).status_code == 404
    assert client.post("/api/v1/files/presign", json={**payload, "project_id": str(project_a_id)}).status_code == 401

    admin_tokens = login(client, admin_email)
    admin_headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}
    assert client.post("/api/v1/files/presign", json={**payload, "project_id": str(project_a_id)}, headers=admin_headers).status_code == 404


def test_login_id_sequence_is_concurrent_safe_and_database_unique() -> None:
    def allocate_login_id() -> str:
        with SessionLocal() as session:
            login_id = generate_login_id(session, "VIEWER")
            session.commit()
            return login_id

    with ThreadPoolExecutor(max_workers=6) as executor:
        login_ids = list(executor.map(lambda _: allocate_login_id(), range(12)))
    assert len(login_ids) == len(set(login_ids)) == 12
    assert all(login_id.startswith("VWR-TN-") for login_id in login_ids)

    with SessionLocal() as session:
        user = create_user(session, "unique-login-id", "VIEWER")
        session.flush()
        duplicate = User(
            login_id=user.login_id,
            email=f"phase-b3-duplicate-{uuid.uuid4().hex}@example.invalid",
            password_hash=hash_password("phase-b3-test-password"),
            full_name="Duplicate Login ID",
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()

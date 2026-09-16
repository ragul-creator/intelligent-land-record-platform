"""Live Phase B.4 project, membership, job, and audit isolation tests."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.audit.service import record_audit
from app.core.auth import hash_password, user_permissions
from app.core.database import SessionLocal
from app.main import app
from app.models import ProcessingJob, Role, User, UserRole
from app.services.user_identities import generate_login_id

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1",
    reason="Set RUN_DATABASE_TESTS=1 after applying migrations to run Phase B.4 integration tests.",
)


def create_user(session, role_name: str, active: bool = True) -> User:
    user = User(
        login_id=generate_login_id(session, role_name),
        email=f"phase-b4-{uuid.uuid4().hex}@example.invalid",
        password_hash=hash_password("phase-b4-test-password"),
        full_name=f"Phase B4 {role_name}",
        is_active=active,
    )
    session.add(user)
    session.flush()
    role = session.scalar(select(Role).where(Role.name == role_name))
    assert role is not None
    session.add(UserRole(user_id=user.id, role_id=role.id))
    return user


def login(client: TestClient, identifier: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": identifier, "password": "phase-b4-test-password"},
    )
    assert response.status_code == 200
    return response.json()


def headers(tokens: dict[str, str]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def assert_error(response, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code


def test_project_membership_job_and_audit_isolation() -> None:
    with SessionLocal() as session:
        admin = create_user(session, "ADMIN")
        officer = create_user(session, "OFFICER")
        outsider = create_user(session, "OFFICER")
        viewer = create_user(session, "VIEWER")
        inactive = create_user(session, "VIEWER", active=False)
        session.commit()
        admin_login_id = admin.login_id
        admin_id = admin.id
        officer_login_id = officer.login_id
        outsider_login_id = outsider.login_id
        officer_id = officer.id
        viewer_id = viewer.id
        inactive_id = inactive.id

    client = TestClient(app)
    admin_headers = headers(login(client, admin_login_id))
    officer_headers = headers(login(client, officer_login_id))
    outsider_headers = headers(login(client, outsider_login_id))

    created = client.post(
        "/api/v1/projects",
        json={"name": f"Phase B4 {uuid.uuid4().hex}", "description": "Project API test"},
        headers=admin_headers,
    )
    assert created.status_code == 201
    project_id = created.json()["id"]

    listed = client.get("/api/v1/projects?limit=1&offset=0", headers=admin_headers)
    assert listed.status_code == 200
    assert project_id in {item["id"] for item in listed.json()["items"]}
    assert client.get("/api/v1/projects", headers=outsider_headers).json()["items"] == []
    assert_error(client.get(f"/api/v1/projects/{project_id}", headers=outsider_headers), 404, "PROJECT_NOT_FOUND")
    assert_error(
        client.patch(f"/api/v1/projects/{project_id}", json={"name": "Blocked"}, headers=outsider_headers),
        404,
        "PROJECT_NOT_FOUND",
    )
    assert_error(
        client.patch(f"/api/v1/projects/{project_id}", json={"owner_id": str(outsider_login_id)}, headers=admin_headers),
        422,
        "VALIDATION_ERROR",
    )

    updated = client.patch(
        f"/api/v1/projects/{project_id}",
        json={"name": "Phase B4 Updated", "state": "ARCHIVED"},
        headers=admin_headers,
    )
    assert updated.status_code == 200
    assert updated.json()["state"] == "ARCHIVED"

    added = client.post(
        f"/api/v1/projects/{project_id}/members",
        json={"user_id": str(officer_id), "role": "OFFICER"},
        headers=admin_headers,
    )
    assert added.status_code == 201
    assert added.json()["login_id"] == officer_login_id
    assert_error(
        client.post(
            f"/api/v1/projects/{project_id}/members",
            json={"user_id": str(officer_id), "role": "OFFICER"},
            headers=admin_headers,
        ),
        409,
        "MEMBERSHIP_EXISTS",
    )
    assert_error(
        client.post(
            f"/api/v1/projects/{project_id}/members",
            json={"user_id": str(inactive_id), "role": "VIEWER"},
            headers=admin_headers,
        ),
        409,
        "USER_INACTIVE",
    )
    assert_error(
        client.post(
            f"/api/v1/projects/{project_id}/members",
            json={"user_id": str(viewer_id), "role": "ADMIN"},
            headers=admin_headers,
        ),
        422,
        "MEMBER_ROLE_INVALID",
    )
    assert_error(
        client.post(
            f"/api/v1/projects/{project_id}/members",
            json={"user_id": str(viewer_id), "role": "VIEWER"},
            headers=officer_headers,
        ),
        403,
        "PROJECT_FORBIDDEN",
    )
    assert_error(
        client.patch(
            f"/api/v1/projects/{project_id}/members/{officer_id}",
            json={"role": "ADMIN"},
            headers=admin_headers,
        ),
        422,
        "MEMBER_ROLE_INVALID",
    )
    members = client.get(f"/api/v1/projects/{project_id}/members?active=true", headers=admin_headers)
    assert members.status_code == 200
    assert {member["user_id"] for member in members.json()["items"]} >= {str(officer_id)}

    with SessionLocal() as session:
        job = ProcessingJob(
            project_id=uuid.UUID(project_id),
            job_type="FILE_REGISTERED",
            idempotency_key=f"phase-b4:{uuid.uuid4()}",
            status="QUEUED",
        )
        session.add(job)
        record_audit(
            session,
            "test.safe_audit",
            "project",
            uuid.UUID(project_id),
            actor_id=admin_id,
            project_id=uuid.UUID(project_id),
            metadata={"safe": "yes", "token": "must-not-persist", "nested": {"signed_url": "must-not-persist"}},
        )
        session.commit()
        job_id = job.id
        assert user_permissions(session, officer_id) != user_permissions(session, viewer_id)

    jobs = client.get(f"/api/v1/projects/{project_id}/jobs?status=QUEUED", headers=officer_headers)
    assert jobs.status_code == 200
    assert str(job_id) in {item["id"] for item in jobs.json()["items"]}
    assert client.get(f"/api/v1/processing-jobs/{job_id}", headers=officer_headers).status_code == 200
    assert_error(client.get(f"/api/v1/processing-jobs/{job_id}", headers=outsider_headers), 404, "PROCESSING_JOB_NOT_FOUND")
    assert_error(client.get(f"/api/v1/projects/{project_id}/jobs?status=UNKNOWN", headers=admin_headers), 422, "VALIDATION_ERROR")

    summary = client.get(f"/api/v1/projects/{project_id}/summary", headers=admin_headers)
    assert summary.status_code == 200
    assert summary.json()["processing_job_count"] >= 1
    workflow = client.get(f"/api/v1/projects/{project_id}/workflow", headers=admin_headers)
    assert workflow.status_code == 200
    assert "QUEUED" in {stage["status"] for stage in workflow.json()["observed_stages"]}
    audit = client.get(f"/api/v1/projects/{project_id}/audit", headers=admin_headers)
    assert audit.status_code == 200
    assert all("token" not in entry["metadata"] for entry in audit.json()["items"])
    assert all("signed_url" not in entry["metadata"].get("nested", {}) for entry in audit.json()["items"])

    assert client.delete(f"/api/v1/projects/{project_id}/members/{officer_id}", headers=admin_headers).status_code == 204
    assert_error(client.get(f"/api/v1/projects/{project_id}", headers=officer_headers), 404, "PROJECT_NOT_FOUND")

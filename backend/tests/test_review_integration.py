"""Live Phase E.2 review queue, RBAC, action, and audit coverage."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth import hash_password
from app.core.database import SessionLocal
from app.models import Project, ProjectMember, Role, User, UserRole
from app.services.review import create_review_task
from app.services.user_identities import generate_login_id


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1",
    reason="Set RUN_DATABASE_TESTS=1 after applying migrations to run E.2 integration tests.",
)


def _user(session, role_name: str) -> User:
    user = User(
        login_id=generate_login_id(session, role_name),
        email=f"e2-{uuid.uuid4().hex}@example.invalid",
        password_hash=hash_password("e2-test-password"),
        full_name=f"E2 {role_name}",
    )
    session.add(user)
    session.flush()
    role = session.scalar(select(Role).where(Role.name == role_name))
    assert role is not None
    session.add(UserRole(user_id=user.id, role_id=role.id))
    return user


def _login(client: TestClient, login_id: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": login_id, "password": "e2-test-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _fixture_data():
    with SessionLocal() as session:
        admin = _user(session, "ADMIN")
        reviewer = _user(session, "REVIEWER")
        second_reviewer = _user(session, "REVIEWER")
        officer = _user(session, "OFFICER")
        viewer = _user(session, "VIEWER")
        project = Project(name=f"E2 {uuid.uuid4().hex}", owner_id=admin.id, state="ACTIVE")
        session.add(project)
        session.flush()
        for user, role in (
            (admin, "ADMIN"),
            (reviewer, "REVIEWER"),
            (second_reviewer, "REVIEWER"),
            (officer, "OFFICER"),
            (viewer, "VIEWER"),
        ):
            session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))

        blocking = create_review_task(
            session,
            project_id=project.id,
            queue_type="GIS",
            target_type="PARCEL",
            target_id=uuid.uuid4(),
            severity="HIGH",
            summary="Unresolved topology conflict.",
            source_refs=["parcel:test"],
            blocking_issue_count=1,
        )
        clear = create_review_task(
            session,
            project_id=project.id,
            queue_type="DOCUMENT",
            target_type="LAND_RECORD",
            target_id=uuid.uuid4(),
            severity="MEDIUM",
            summary="Low-confidence extracted field.",
            source_refs=["document:page1"],
            blocking_issue_count=0,
        )
        session.commit()
        return {
            "project_id": project.id,
            "blocking_id": blocking.id,
            "clear_id": clear.id,
            "reviewer": reviewer.login_id,
            "second_reviewer": second_reviewer.login_id,
            "second_reviewer_id": second_reviewer.id,
            "officer": officer.login_id,
            "viewer": viewer.login_id,
        }


def test_review_queue_is_project_scoped_and_rbac_enforced() -> None:
    from app.main import app

    data = _fixture_data()
    client = TestClient(app)
    reviewer_headers = _login(client, data["reviewer"])
    officer_headers = _login(client, data["officer"])
    viewer_headers = _login(client, data["viewer"])

    listed = client.get(
        f"/api/v1/review/tasks?project_id={data['project_id']}&status=OPEN",
        headers=reviewer_headers,
    )
    assert listed.status_code == 200
    assert listed.json()["page"]["total"] == 2

    assert client.get(
        f"/api/v1/review/tasks?project_id={data['project_id']}",
        headers=viewer_headers,
    ).status_code == 403

    assert client.get(
        f"/api/v1/review/tasks/{data['clear_id']}",
        headers=officer_headers,
    ).status_code == 200
    assert client.patch(
        f"/api/v1/review/tasks/{data['clear_id']}",
        json={"action": "COMMENT", "reason": "Officer cannot act."},
        headers=officer_headers,
    ).status_code == 403


def test_review_actions_block_unsafe_approval_and_persist_history() -> None:
    from app.main import app

    data = _fixture_data()
    client = TestClient(app)
    reviewer_headers = _login(client, data["reviewer"])

    blocked = client.patch(
        f"/api/v1/review/tasks/{data['blocking_id']}",
        json={"action": "APPROVE"},
        headers=reviewer_headers,
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "REVIEW_ACTION_INVALID"

    comment = client.patch(
        f"/api/v1/review/tasks/{data['blocking_id']}",
        json={"action": "COMMENT", "reason": "Needs survey evidence."},
        headers=reviewer_headers,
    )
    assert comment.status_code == 200
    assert comment.json()["status"] == "OPEN"

    approved = client.patch(
        f"/api/v1/review/tasks/{data['clear_id']}",
        json={"action": "APPROVE"},
        headers=reviewer_headers,
    )
    assert approved.status_code == 200
    body = approved.json()
    assert body["status"] == "RESOLVED"
    assert body["resolution_action"] == "APPROVE"
    assert any(event["metadata"].get("action") == "APPROVE" for event in body["history"])


def test_escalation_assignment_and_correction_contract() -> None:
    from app.main import app

    data = _fixture_data()
    client = TestClient(app)
    reviewer_headers = _login(client, data["reviewer"])

    escalated = client.patch(
        f"/api/v1/review/tasks/{data['blocking_id']}",
        json={
            "action": "ESCALATE",
            "reason": "Senior review required.",
            "assignee_user_id": str(data["second_reviewer_id"]),
        },
        headers=reviewer_headers,
    )
    assert escalated.status_code == 200
    assert escalated.json()["escalated"] is True
    assert escalated.json()["assignee_user_id"] == str(data["second_reviewer_id"])

    bad_correction = client.patch(
        f"/api/v1/review/tasks/{data['blocking_id']}",
        json={"action": "CORRECT", "reason": "Adjusted field."},
        headers=reviewer_headers,
    )
    assert bad_correction.status_code == 409

    corrected = client.patch(
        f"/api/v1/review/tasks/{data['blocking_id']}",
        json={
            "action": "CORRECT",
            "reason": "Adjusted field against source evidence.",
            "correction_reference": "field-version:2",
        },
        headers=reviewer_headers,
    )
    assert corrected.status_code == 200
    assert corrected.json()["status"] == "OPEN"


def test_inactive_reviewer_cannot_be_assigned_or_escalated() -> None:
    from app.main import app

    data = _fixture_data()
    with SessionLocal() as session:
        second = session.scalar(select(User).where(User.login_id == data["second_reviewer"]))
        assert second is not None
        second.is_active = False
        session.commit()

    client = TestClient(app)
    reviewer_headers = _login(client, data["reviewer"])
    response = client.patch(
        f"/api/v1/review/tasks/{data['blocking_id']}",
        json={
            "action": "ESCALATE",
            "reason": "Inactive reviewers must not receive active work.",
            "assignee_user_id": str(data["second_reviewer_id"]),
        },
        headers=reviewer_headers,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "REVIEW_ACTION_INVALID"
    assert "project member with review:act permission" in response.json()["error"]["message"]

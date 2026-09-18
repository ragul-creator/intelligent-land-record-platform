"""Live PostgreSQL coverage for the Phase G.2 project dashboard."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth import hash_password
from app.core.database import SessionLocal
from app.main import app
from app.models import (
    Document, DocumentOcrResultRecord, DocumentValidationResultRecord, File, Parcel,
    ProcessingJob, Project, ProjectMember, RecordParcelLink, ReviewTask, Role, User, UserRole,
)
from app.services.user_identities import generate_login_id

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1",
    reason="Set RUN_DATABASE_TESTS=1 after applying migrations to run dashboard integration tests.",
)


def _user(session, role_name: str) -> User:
    user = User(
        login_id=generate_login_id(session, role_name),
        email=f"g2-{uuid.uuid4().hex}@example.invalid",
        password_hash=hash_password("g2-test-password"),
        full_name=f"G2 {role_name}",
    )
    session.add(user)
    session.flush()
    role = session.scalar(select(Role).where(Role.name == role_name))
    assert role is not None
    session.add(UserRole(user_id=user.id, role_id=role.id))
    return user


def _project(session, owner: User, *members: tuple[User, str]) -> Project:
    project = Project(name=f"G2 {uuid.uuid4().hex}", owner_id=owner.id, state="ACTIVE")
    session.add(project)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="OFFICER"))
    for user, role in members:
        session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    return project


def _validated_document_with_link(session, project: Project, actor: User) -> None:
    source = File(
        project_id=project.id, original_name="validated.pdf", category="DOCUMENT",
        mime_type="application/pdf", size_bytes=10, sha256="c" * 64,
        storage_key=f"g2/{uuid.uuid4()}", status="UPLOADED",
    )
    session.add(source)
    session.flush()
    document = Document(project_id=project.id, file_id=source.id, uploaded_by_user_id=actor.id, status="VALIDATED")
    session.add(document)
    session.flush()
    job = ProcessingJob(project_id=project.id, job_type="DOCUMENT_AI_PROCESS", idempotency_key=f"g2:{uuid.uuid4()}", status="COMPLETED")
    session.add(job)
    session.flush()
    ocr = DocumentOcrResultRecord(
        document_id=document.id, processing_job_id=job.id, version=1, status="COMPLETED",
        requested_languages_json=["tam", "eng"], project_tested_languages_json=["tam", "eng"],
        engine="mock", page_count=1, payload_json={}, processed_at=datetime.now(UTC),
    )
    session.add(ocr)
    session.flush()
    validation = DocumentValidationResultRecord(
        document_id=document.id, ocr_result_id=ocr.id, processing_job_id=job.id, version=1,
        status="VALID", validation_version="g2-test", report_json={}, confidence_summary_json={},
        checks_json={}, processed_at=datetime.now(UTC),
    )
    session.add(validation)
    session.flush()
    parcel = Parcel(
        project_id=project.id, external_identifier="123/4", source="CADASTRAL_GIS",
        source_reference="g2-fixture", status="DRAFT", verification_status="UNVERIFIED",
        current_geometry_version=1, coordinate_space="WORLD", source_crs="EPSG:4326",
    )
    session.add(parcel)
    session.flush()
    session.add(RecordParcelLink(
        project_id=project.id, document_id=document.id, document_validation_result_id=validation.id,
        parcel_id=parcel.id, link_status="CONFIRMED", link_method="MANUAL", confidence=1.0,
        rationale_json={"fixture": True}, provenance_json={"fixture": True}, created_by_user_id=actor.id,
    ))


def _headers(client: TestClient, login_id: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"identifier": login_id, "password": "g2-test-password"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_dashboard_counts_are_project_scoped_and_viewer_readable() -> None:
    with SessionLocal() as session:
        officer = _user(session, "OFFICER")
        viewer = _user(session, "VIEWER")
        outsider = _user(session, "VIEWER")
        project = _project(session, officer, (viewer, "VIEWER"))
        other = _project(session, outsider)
        _validated_document_with_link(session, project, officer)
        _validated_document_with_link(session, other, outsider)
        session.add(ProcessingJob(project_id=project.id, job_type="TEST", idempotency_key=f"g2-failed:{uuid.uuid4()}", status="FAILED"))
        session.add(ReviewTask(
            project_id=project.id, queue_type="DOCUMENT", target_type="DOCUMENT", target_id=uuid.uuid4(),
            severity="HIGH", status="OPEN", summary="Needs reviewer attention", source_refs_json=[],
            metadata_json={}, blocking_issue_count=1, assignee_user_id=officer.id, created_by_user_id=officer.id,
        ))
        session.commit()
        project_id = project.id
        viewer_login = viewer.login_id
        outsider_login = outsider.login_id

    client = TestClient(app)
    response = client.get(f"/api/v1/projects/{project_id}/dashboard", headers=_headers(client, viewer_login))
    assert response.status_code == 200
    body = response.json()
    assert body["project_role"] == "VIEWER"
    assert body["documents"]["total"] == 1
    assert body["documents"]["validated_records"] == 1
    assert body["documents"]["unlinked_validated_records"] == 0
    assert body["record_parcel_links"]["confirmed_records"] == 1
    assert body["geo"]["parcels"] == 1
    assert body["reviews"]["open"] == 1
    assert body["reviews"]["high"] == 1
    assert body["attention"]["failed_jobs"] == 1
    assert body["attention"]["high_open_reviews"] == 1

    denied = client.get(f"/api/v1/projects/{project_id}/dashboard", headers=_headers(client, outsider_login))
    assert denied.status_code == 404

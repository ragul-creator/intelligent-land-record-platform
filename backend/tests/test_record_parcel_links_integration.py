"""Live PostgreSQL coverage for Phase G.1 record-to-parcel links."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.auth import hash_password
from app.core.database import SessionLocal
from app.models import (
    AuditLog,
    Document,
    DocumentExtractedField,
    DocumentOcrResultRecord,
    DocumentValidationResultRecord,
    File,
    Parcel,
    ParcelGeometryVersion,
    ProcessingJob,
    Project,
    ProjectMember,
    RecordParcelLink,
    ReviewTask,
    Role,
    User,
    UserRole,
)
from app.services.user_identities import generate_login_id


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1",
    reason="Set RUN_DATABASE_TESTS=1 after applying the G.1 migration to run record-to-parcel integration tests.",
)


def _user(session, role_name: str) -> User:
    user = User(
        login_id=generate_login_id(session, role_name),
        email=f"g1-{uuid.uuid4().hex}@example.invalid",
        password_hash=hash_password("g1-test-password"),
        full_name=f"G1 {role_name}",
    )
    session.add(user)
    session.flush()
    role = session.scalar(select(Role).where(Role.name == role_name))
    assert role is not None
    session.add(UserRole(user_id=user.id, role_id=role.id))
    return user


def _project(session, owner: User, *members: tuple[User, str]) -> Project:
    project = Project(name=f"G1 {uuid.uuid4().hex}", owner_id=owner.id, state="ACTIVE")
    session.add(project)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="OFFICER"))
    for user, role in members:
        session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    return project


def _record(session, project: Project, user: User, survey: str = "123/4") -> tuple[Document, DocumentValidationResultRecord]:
    source = File(
        project_id=project.id,
        original_name="record.pdf",
        category="DOCUMENT",
        mime_type="application/pdf",
        size_bytes=10,
        sha256="a" * 64,
        storage_key=f"g1/{uuid.uuid4()}",
        status="UPLOADED",
    )
    session.add(source)
    session.flush()
    document = Document(project_id=project.id, file_id=source.id, uploaded_by_user_id=user.id, status="VALIDATED")
    session.add(document)
    session.flush()
    job = ProcessingJob(project_id=project.id, job_type="DOCUMENT_AI_PROCESS", idempotency_key=f"g1:{uuid.uuid4()}", status="COMPLETED")
    session.add(job)
    session.flush()
    ocr = DocumentOcrResultRecord(
        document_id=document.id,
        processing_job_id=job.id,
        version=1,
        status="COMPLETED",
        requested_languages_json=["tam", "eng"],
        project_tested_languages_json=["tam", "eng"],
        engine="mock",
        page_count=1,
        payload_json={"source_id": str(document.id)},
        processed_at=datetime.now(UTC),
    )
    session.add(ocr)
    session.flush()
    for index, (name, value, normalized) in enumerate(
        (
            ("survey_number", survey, survey),
            ("village", "Sample Village", "Sample Village"),
            ("district", "Chennai", "Chennai"),
            ("plot_area", "1200 sq ft", {"value": 1200, "unit": "sq_ft"}),
        )
    ):
        session.add(
            DocumentExtractedField(
                document_id=document.id,
                ocr_result_id=ocr.id,
                candidate_index=index,
                field_name=name,
                original_value=value,
                normalized_value_json=normalized,
                confidence=0.95,
                page_number=1,
                bounding_box_json={"left": 10 + index, "top": 20, "width": 30, "height": 12},
                source_id=str(document.id),
                model_version="f1",
                extractor_version="f2",
                processed_at=datetime.now(UTC),
            )
        )
    validation = DocumentValidationResultRecord(
        document_id=document.id,
        ocr_result_id=ocr.id,
        processing_job_id=job.id,
        version=1,
        status="VALID",
        validation_version="f3",
        report_json={},
        confidence_summary_json={},
        checks_json={},
        processed_at=datetime.now(UTC),
    )
    session.add(validation)
    session.flush()
    return document, validation


def _parcel(session, project: Project, identifier: str, *, district: str = "Chennai") -> Parcel:
    parcel = Parcel(
        project_id=project.id,
        external_identifier=identifier,
        source="CADASTRAL_GIS",
        source_reference="g1-fixture",
        status="DRAFT",
        verification_status="UNVERIFIED",
        current_geometry_version=1,
        coordinate_space="WORLD",
        source_crs="EPSG:4326",
    )
    session.add(parcel)
    session.flush()
    session.add(
        ParcelGeometryVersion(
            parcel_id=parcel.id,
            version=1,
            source_geometry_json={"properties": {"village": "Sample Village", "district": district}},
            source="CADASTRAL_GIS",
            source_reference="g1-fixture",
            coordinate_space="WORLD",
            source_crs="EPSG:4326",
            area_m2=1200 / 10.7639104167,
            area_sqft=1200.0,
            validation_status="VALID",
            created_by_type="IMPORT",
        )
    )
    return parcel


def _headers(client: TestClient, login_id: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"identifier": login_id, "password": "g1-test-password"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_exact_link_round_trips_provenance_and_reverse_lookup() -> None:
    from app.main import app

    with SessionLocal() as session:
        officer, viewer, outsider = _user(session, "OFFICER"), _user(session, "VIEWER"), _user(session, "OFFICER")
        project = _project(session, officer, (viewer, "VIEWER"))
        other_project = _project(session, outsider)
        document, validation = _record(session, project, officer)
        parcel = _parcel(session, project, "123/4")
        other_parcel = _parcel(session, other_project, "123/4")
        session.commit()
        ids = (project.id, document.id, validation.id, parcel.id, other_parcel.id)
        logins = (officer.login_id, viewer.login_id, outsider.login_id)

    client = TestClient(app)
    officer_headers, viewer_headers, outsider_headers = (_headers(client, login) for login in logins)
    suggested = client.post(
        f"/api/v1/projects/{ids[0]}/documents/{ids[1]}/record-parcel-links/suggestions",
        headers=officer_headers,
        json={"document_validation_result_id": str(ids[2])},
    )
    assert suggested.status_code == 201
    item = suggested.json()["items"][0]
    assert item["link_status"] == "CONFIRMED"
    assert item["parcel_id"] == str(ids[3])
    assert item["provenance"]["document_fields"][0]["page_number"] == 1
    assert client.get(f"/api/v1/projects/{ids[0]}/documents/{ids[1]}/record-parcel-links", headers=viewer_headers).json()["page"]["total"] == 1
    reverse = client.get(f"/api/v1/projects/{ids[0]}/parcels/{ids[3]}/record-parcel-links", headers=viewer_headers)
    assert reverse.status_code == 200 and reverse.json()["items"][0]["id"] == item["id"]
    assert client.get(f"/api/v1/projects/{ids[0]}/documents/{ids[1]}/record-parcel-links", headers=outsider_headers).status_code == 404
    assert client.post(
        f"/api/v1/projects/{ids[0]}/documents/{ids[1]}/record-parcel-links/manual",
        headers=officer_headers,
        json={"document_validation_result_id": str(ids[2]), "parcel_id": str(ids[4]), "rationale": "Cross-project attempt"},
    ).status_code == 422
    assert client.post(
        f"/api/v1/projects/{ids[0]}/documents/{ids[1]}/record-parcel-links/manual",
        headers=viewer_headers,
        json={"document_validation_result_id": str(ids[2]), "parcel_id": str(ids[3]), "rationale": "Viewer attempt"},
    ).status_code == 403
    with SessionLocal() as session:
        assert session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "record_parcel_link.auto_confirmed")) >= 1


def test_ambiguous_links_create_one_review_task_each_and_rejection_preserves_history() -> None:
    from app.main import app

    with SessionLocal() as session:
        officer, reviewer, viewer = _user(session, "OFFICER"), _user(session, "REVIEWER"), _user(session, "VIEWER")
        project = _project(session, officer, (reviewer, "REVIEWER"), (viewer, "VIEWER"))
        document, validation = _record(session, project, officer)
        _parcel(session, project, "123/4")
        _parcel(session, project, "123/4")
        manual_parcel = _parcel(session, project, "999/1")
        session.commit()
        project_id, document_id, validation_id, manual_parcel_id = project.id, document.id, validation.id, manual_parcel.id
        officer_login, reviewer_login, viewer_login = officer.login_id, reviewer.login_id, viewer.login_id

    client = TestClient(app)
    officer_headers, reviewer_headers, viewer_headers = (_headers(client, login) for login in (officer_login, reviewer_login, viewer_login))
    url = f"/api/v1/projects/{project_id}/documents/{document_id}/record-parcel-links/suggestions"
    first = client.post(url, headers=officer_headers, json={"document_validation_result_id": str(validation_id)})
    repeated = client.post(url, headers=officer_headers, json={"document_validation_result_id": str(validation_id)})
    assert first.status_code == repeated.status_code == 201
    assert len(first.json()["items"]) == len(repeated.json()["items"]) == 2
    links = first.json()["items"]
    assert {item["link_status"] for item in links} == {"REVIEW_REQUIRED"}
    assert all(item["review_task_id"] for item in links)
    assert client.post(f"/api/v1/projects/{project_id}/record-parcel-links/{links[0]['id']}/confirm", headers=viewer_headers, json={}).status_code == 403
    rejected = client.post(
        f"/api/v1/projects/{project_id}/record-parcel-links/{links[0]['id']}/reject",
        headers=reviewer_headers,
        json={"reason": "Duplicate cadastral identifier needs source review."},
    )
    assert rejected.status_code == 200 and rejected.json()["link_status"] == "REJECTED"
    manual = client.post(
        f"/api/v1/projects/{project_id}/documents/{document_id}/record-parcel-links/manual",
        headers=officer_headers,
        json={
            "document_validation_result_id": str(validation_id),
            "parcel_id": str(manual_parcel_id),
            "rationale": "Authorized officer matched the surveyed record to the supplied cadastral source.",
        },
    )
    assert manual.status_code == 201 and manual.json()["link_status"] == "CONFIRMED"
    assert manual.json()["link_method"] == "MANUAL"
    with SessionLocal() as session:
        assert session.scalar(select(func.count(RecordParcelLink.id)).where(RecordParcelLink.document_validation_result_id == validation_id)) == 3
        assert session.scalar(select(func.count(ReviewTask.id)).where(ReviewTask.target_type == "RECORD_PARCEL_LINK")) >= 2
        rejected_row = session.get(RecordParcelLink, uuid.UUID(links[0]["id"]))
        assert rejected_row is not None and rejected_row.provenance_json["document_validation_result_id"] == str(validation_id)
        assert session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "record_parcel_link.manually_confirmed")) >= 1

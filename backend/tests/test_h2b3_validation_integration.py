"""Live H.2B.3 duplicate-record, area-mismatch, and issue API coverage."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth import hash_password
from app.core.database import SessionLocal
from app.models import (
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
    ReviewTask,
    Role,
    User,
    UserRole,
)
from app.services.user_identities import generate_login_id


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1",
    reason="Set RUN_DATABASE_TESTS=1 after applying migrations to run H.2B.3 integration tests.",
)


def _user(session, role_name: str) -> User:
    user = User(
        login_id=generate_login_id(session, role_name),
        email=f"h2b3-{uuid.uuid4().hex}@example.invalid",
        password_hash=hash_password("h2b3-test-password"),
        full_name=f"H2B3 {role_name}",
    )
    session.add(user)
    session.flush()
    role = session.scalar(select(Role).where(Role.name == role_name))
    assert role is not None
    session.add(UserRole(user_id=user.id, role_id=role.id))
    return user


def _project(session, owner: User, *members: tuple[User, str]) -> Project:
    project = Project(name=f"H2B3 {uuid.uuid4().hex}", owner_id=owner.id, state="ACTIVE")
    session.add(project)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="OFFICER"))
    for user, role in members:
        session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    return project


def _record(
    session,
    project: Project,
    user: User,
    *,
    survey: str,
    area_sqft: float,
) -> tuple[Document, DocumentValidationResultRecord]:
    source = File(
        project_id=project.id,
        original_name=f"{survey.replace('/', '-')}.pdf",
        category="DOCUMENT",
        mime_type="application/pdf",
        size_bytes=10,
        sha256=uuid.uuid4().hex * 2,
        storage_key=f"h2b3/{uuid.uuid4()}",
        status="UPLOADED",
    )
    session.add(source)
    session.flush()
    document = Document(
        project_id=project.id,
        file_id=source.id,
        uploaded_by_user_id=user.id,
        status="VALIDATED",
    )
    session.add(document)
    session.flush()
    job = ProcessingJob(
        project_id=project.id,
        job_type="DOCUMENT_AI_PROCESS",
        idempotency_key=f"h2b3:{uuid.uuid4()}",
        status="COMPLETED",
    )
    session.add(job)
    session.flush()
    ocr = DocumentOcrResultRecord(
        document_id=document.id,
        processing_job_id=job.id,
        version=1,
        status="COMPLETED",
        requested_languages_json=["eng"],
        project_tested_languages_json=["eng"],
        engine="mock",
        page_count=1,
        payload_json={"source_id": str(document.id)},
        processed_at=datetime.now(UTC),
    )
    session.add(ocr)
    session.flush()
    fields = (
        ("survey_number", survey, survey),
        ("village", "Sample Village", "Sample Village"),
        ("district", "Chennai", "Chennai"),
        ("plot_area", f"{area_sqft:g} sq ft", {"value": area_sqft, "unit": "sq_ft"}),
    )
    for index, (name, value, normalized) in enumerate(fields):
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
                model_version="h2b3",
                extractor_version="h2b3",
                processed_at=datetime.now(UTC),
            )
        )
    validation = DocumentValidationResultRecord(
        document_id=document.id,
        ocr_result_id=ocr.id,
        processing_job_id=job.id,
        version=1,
        status="VALID",
        validation_version="h2b3",
        report_json={},
        confidence_summary_json={},
        checks_json={},
        processed_at=datetime.now(UTC),
    )
    session.add(validation)
    session.flush()
    return document, validation


def _parcel(session, project: Project, *, survey: str, area_sqft: float) -> Parcel:
    parcel = Parcel(
        project_id=project.id,
        external_identifier=survey,
        source="CADASTRAL_GIS",
        source_reference="h2b3-fixture",
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
            source_geometry_json={
                "properties": {"village": "Sample Village", "district": "Chennai"}
            },
            source="CADASTRAL_GIS",
            source_reference="h2b3-fixture",
            coordinate_space="WORLD",
            source_crs="EPSG:4326",
            area_m2=area_sqft / 10.7639104167,
            area_sqft=area_sqft,
            validation_status="VALID",
            created_by_type="IMPORT",
        )
    )
    return parcel


def _headers(client: TestClient, login_id: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": login_id, "password": "h2b3-test-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_validation_run_creates_idempotent_duplicate_and_area_issues() -> None:
    from app.main import app

    with SessionLocal() as session:
        officer = _user(session, "OFFICER")
        reviewer = _user(session, "REVIEWER")
        viewer = _user(session, "VIEWER")
        project = _project(session, officer, (reviewer, "REVIEWER"), (viewer, "VIEWER"))
        first_document, first_validation = _record(
            session,
            project,
            officer,
            survey="123/4",
            area_sqft=1200,
        )
        second_document, second_validation = _record(
            session,
            project,
            officer,
            survey="123 / 4",
            area_sqft=1200,
        )
        parcel = _parcel(session, project, survey="123/4", area_sqft=700)
        session.commit()
        ids = (
            project.id,
            first_document.id,
            first_validation.id,
            second_document.id,
            second_validation.id,
            parcel.id,
        )
        logins = (reviewer.login_id, viewer.login_id)

    client = TestClient(app)
    reviewer_headers, viewer_headers = (_headers(client, login) for login in logins)

    for document_id, validation_id in ((ids[1], ids[2]), (ids[3], ids[4])):
        linked = client.post(
            f"/api/v1/projects/{ids[0]}/documents/{document_id}/record-parcel-links/suggestions",
            headers=reviewer_headers,
            json={"document_validation_result_id": str(validation_id)},
        )
        assert linked.status_code == 201
        assert linked.json()["items"][0]["link_status"] == "CONFIRMED"
        assert linked.json()["items"][0]["parcel_id"] == str(ids[5])

    first_run = client.post(
        f"/api/v1/projects/{ids[0]}/validation/run",
        headers=reviewer_headers,
    )
    assert first_run.status_code == 201
    body = first_run.json()
    assert body["created_count"] == 3
    assert body["refreshed_count"] == 0
    assert body["open_issue_count"] == 3
    assert body["duplicate_record_issue_count"] == 1
    assert body["area_mismatch_issue_count"] == 2

    duplicate_list = client.get(
        f"/api/v1/projects/{ids[0]}/validation/issues?issue_type=DUPLICATE_RECORD&status=OPEN",
        headers=reviewer_headers,
    )
    assert duplicate_list.status_code == 200
    assert duplicate_list.json()["page"]["total"] == 1
    duplicate = duplicate_list.json()["items"][0]
    assert duplicate["target_type"] == "VALIDATION_ISSUE"
    assert duplicate["metadata"]["normalized_identifier"] == "123/4"
    assert set(duplicate["metadata"]["document_ids"]) == {str(ids[1]), str(ids[3])}

    mismatch_list = client.get(
        f"/api/v1/projects/{ids[0]}/validation/issues?issue_type=AREA_MISMATCH&status=OPEN",
        headers=reviewer_headers,
    )
    assert mismatch_list.status_code == 200
    assert mismatch_list.json()["page"]["total"] == 2
    assert all(item["metadata"]["relative_difference"] > 0.05 for item in mismatch_list.json()["items"])

    second_run = client.post(
        f"/api/v1/projects/{ids[0]}/validation/run",
        headers=reviewer_headers,
    )
    assert second_run.status_code == 201
    assert second_run.json()["created_count"] == 0
    assert second_run.json()["refreshed_count"] == 3
    assert second_run.json()["open_issue_count"] == 3

    assert client.get(
        f"/api/v1/projects/{ids[0]}/validation/issues",
        headers=viewer_headers,
    ).status_code == 403

    with SessionLocal() as session:
        tasks = list(
            session.scalars(
                select(ReviewTask).where(
                    ReviewTask.project_id == ids[0],
                    ReviewTask.target_type == "VALIDATION_ISSUE",
                )
            )
        )
        assert len(tasks) == 3

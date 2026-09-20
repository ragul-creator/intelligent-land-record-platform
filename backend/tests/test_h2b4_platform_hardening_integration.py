"""Live H.2B.4 platform-hardening coverage for search, exports, jobs, audit, admin, and Viewer policy."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from geoalchemy2.shape import from_shape
from shapely.geometry import Polygon
from sqlalchemy import select

from app.core.auth import hash_password
from app.core.database import SessionLocal
from app.models import (
    AuditLog,
    Document,
    DocumentExtractedField,
    DocumentOcrResultRecord,
    DocumentProcessingJob,
    File,
    Parcel,
    ParcelGeometryVersion,
    ProcessingJob,
    Project,
    ProjectMember,
    Role,
    User,
    UserRole,
)
from app.services.user_identities import generate_login_id


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1",
    reason="Set RUN_DATABASE_TESTS=1 after applying migrations to run H.2B.4 integration tests.",
)


def _user(session, role_name: str) -> User:
    user = User(
        login_id=generate_login_id(session, role_name),
        email=f"h2b4-{role_name.lower()}-{uuid.uuid4().hex}@example.invalid",
        password_hash=hash_password("h2b4-test-password"),
        full_name=f"H2B4 {role_name}",
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
        json={"identifier": login_id, "password": "h2b4-test-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _seed_project(session):
    officer = _user(session, "OFFICER")
    admin = _user(session, "ADMIN")
    viewer = _user(session, "VIEWER")
    project = Project(
        name=f"H2B4 {uuid.uuid4().hex}",
        description="Platform hardening integration fixture",
        owner_id=officer.id,
        state="ACTIVE",
    )
    session.add(project)
    session.flush()
    session.add_all(
        [
            ProjectMember(project_id=project.id, user_id=officer.id, role="OFFICER"),
            ProjectMember(project_id=project.id, user_id=admin.id, role="ADMIN"),
            ProjectMember(project_id=project.id, user_id=viewer.id, role="VIEWER"),
        ]
    )

    source = File(
        project_id=project.id,
        original_name="survey-123-evidence.pdf",
        category="DOCUMENT",
        mime_type="application/pdf",
        size_bytes=20,
        sha256=uuid.uuid4().hex * 2,
        storage_key=f"h2b4/{uuid.uuid4()}",
        status="UPLOADED",
    )
    session.add(source)
    session.flush()
    document = Document(
        project_id=project.id,
        file_id=source.id,
        uploaded_by_user_id=officer.id,
        status="REVIEW_REQUIRED",
    )
    session.add(document)
    session.flush()

    completed_job = ProcessingJob(
        project_id=project.id,
        job_type="DOCUMENT_AI_PROCESS",
        idempotency_key=f"h2b4-complete:{uuid.uuid4()}",
        status="COMPLETED",
        progress=100,
    )
    session.add(completed_job)
    session.flush()
    ocr = DocumentOcrResultRecord(
        document_id=document.id,
        processing_job_id=completed_job.id,
        version=1,
        status="COMPLETED",
        requested_languages_json=["eng"],
        project_tested_languages_json=["eng"],
        engine="fixture",
        page_count=1,
        confidence=0.91,
        payload_json={"source_id": str(document.id)},
        processed_at=datetime.now(UTC),
    )
    session.add(ocr)
    session.flush()
    session.add(
        DocumentExtractedField(
            document_id=document.id,
            ocr_result_id=ocr.id,
            candidate_index=0,
            field_name="survey_number",
            original_value="123/4",
            normalized_value_json="123/4",
            confidence=0.91,
            page_number=1,
            source_id=str(document.id),
            extractor_version="h2b4-fixture",
            processed_at=datetime.now(UTC),
        )
    )

    parcel = Parcel(
        project_id=project.id,
        external_identifier="P-123/4",
        source="CADASTRAL_GIS",
        source_reference="h2b4-fixture",
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
            geometry=from_shape(
                Polygon(
                    [
                        (80.0000, 13.0000),
                        (80.0004, 13.0000),
                        (80.0004, 13.0004),
                        (80.0000, 13.0004),
                        (80.0000, 13.0000),
                    ]
                ),
                srid=4326,
            ),
            source_geometry_json=None,
            source="CADASTRAL_GIS",
            source_reference="h2b4-fixture",
            coordinate_space="WORLD",
            source_crs="EPSG:4326",
            area_m2=1900.0,
            area_sqft=20451.4,
            validation_status="VALID",
            created_by_type="IMPORT",
        )
    )

    failed_job = ProcessingJob(
        project_id=project.id,
        job_type="DOCUMENT_AI_PROCESS",
        idempotency_key=f"h2b4-failed:{uuid.uuid4()}",
        status="FAILED",
        progress=25,
        retry_count=2,
        error_json={"message": "Processing failed."},
    )
    session.add(failed_job)
    session.flush()
    session.add(
        DocumentProcessingJob(
            id=failed_job.id,
            document_id=document.id,
            requested_languages_json=["eng"],
            processing_version=2,
            job_type="DOCUMENT_AI_PROCESS",
            requested_by_user_id=officer.id,
        )
    )
    session.commit()
    return {
        "project_id": project.id,
        "document_id": document.id,
        "parcel_id": parcel.id,
        "failed_job_id": failed_job.id,
        "officer_login": officer.login_id,
        "admin_login": admin.login_id,
        "viewer_login": viewer.login_id,
    }


def test_h2b4_search_exports_dashboard_viewer_policy_and_job_recovery(monkeypatch) -> None:
    from app.main import app
    from app.workers.tasks import process_document_ai

    with SessionLocal() as session:
        ids = _seed_project(session)

    client = TestClient(app)
    viewer_headers = _headers(client, ids["viewer_login"])
    officer_headers = _headers(client, ids["officer_login"])

    search_response = client.get(
        f"/api/v1/projects/{ids['project_id']}/search?q=123",
        headers=viewer_headers,
    )
    assert search_response.status_code == 200
    search_items = search_response.json()["items"]
    assert {item["kind"] for item in search_items} >= {"DOCUMENT", "PARCEL", "FIELD"}
    assert all(item["preliminary"] for item in search_items)

    dashboard = client.get(
        f"/api/v1/projects/{ids['project_id']}/dashboard",
        headers=viewer_headers,
    )
    assert dashboard.status_code == 200
    dashboard_body = dashboard.json()
    assert dashboard_body["visibility"]["viewer_read_only"] is True
    assert dashboard_body["visibility"]["draft_data_visible"] is True
    assert dashboard_body["jobs"]["failed"] == 1
    assert dashboard_body["jobs"]["retryable_failed"] == 1

    records = client.get(
        f"/api/v1/projects/{ids['project_id']}/exports/records.csv",
        headers=viewer_headers,
    )
    assert records.status_code == 200
    assert "123/4" in records.text
    assert "not statutory ownership proof" in records.text

    parcels = client.get(
        f"/api/v1/projects/{ids['project_id']}/exports/parcels.geojson",
        headers=viewer_headers,
    )
    assert parcels.status_code == 200
    parcel_body = parcels.json()
    assert parcel_body["type"] == "FeatureCollection"
    assert len(parcel_body["features"]) == 1
    assert parcel_body["features"][0]["properties"]["preliminary"] is True
    assert parcel_body["features"][0]["properties"]["legal_boundary_asserted"] is False

    # Viewer can inspect failed work but cannot invoke the mutation.
    assert client.post(
        f"/api/v1/processing-jobs/{ids['failed_job_id']}/retry",
        headers=viewer_headers,
    ).status_code == 403

    dispatched: list[str] = []
    monkeypatch.setattr(process_document_ai, "delay", lambda job_id: dispatched.append(job_id))
    retried = client.post(
        f"/api/v1/processing-jobs/{ids['failed_job_id']}/retry",
        headers=officer_headers,
    )
    assert retried.status_code == 202
    assert retried.json()["status"] == "QUEUED"
    assert retried.json()["retry_count"] == 3
    assert retried.json()["has_error"] is False
    assert dispatched == [str(ids["failed_job_id"])]

    audit = client.get(
        f"/api/v1/projects/{ids['project_id']}/audit?action=processing_job.manual_retry_queued",
        headers=officer_headers,
    )
    assert audit.status_code == 200
    assert audit.json()["page"]["total"] == 1
    assert audit.json()["items"][0]["action"] == "processing_job.manual_retry_queued"

    with SessionLocal() as session:
        row = session.get(ProcessingJob, ids["failed_job_id"])
        assert row is not None
        assert row.status == "QUEUED"
        assert row.retry_count == 3
        action = session.scalar(
            select(AuditLog.action).where(
                AuditLog.project_id == ids["project_id"],
                AuditLog.action == "processing_job.manual_retry_queued",
            )
        )
        assert action == "processing_job.manual_retry_queued"


def test_h2b4_user_directory_is_admin_only() -> None:
    from app.main import app

    with SessionLocal() as session:
        ids = _seed_project(session)

    client = TestClient(app)
    admin_headers = _headers(client, ids["admin_login"])
    viewer_headers = _headers(client, ids["viewer_login"])

    directory = client.get("/api/v1/users?q=H2B4&active=true", headers=admin_headers)
    assert directory.status_code == 200
    assert directory.json()["page"]["total"] >= 3
    assert any(item["login_id"] == ids["viewer_login"] for item in directory.json()["items"])

    denied = client.get("/api/v1/users?q=H2B4", headers=viewer_headers)
    assert denied.status_code == 403

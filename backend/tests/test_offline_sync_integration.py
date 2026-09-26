"""Database-backed integration tests for Offline-First Sync and Conflict Resolution."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import Polygon, mapping
from sqlalchemy import select

from app.core.auth import hash_password
from app.core.database import SessionLocal
from app.main import app
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
    RecordParcelLink,
    ReviewTask,
    Role,
    SyncChange,
    SyncOperation,
    User,
    UserRole,
)
from app.services.user_identities import generate_login_id

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1",
    reason="Set RUN_DATABASE_TESTS=1 after applying migrations to run offline sync integration tests.",
)


def _user(session, role_name: str) -> User:
    user = User(
        login_id=generate_login_id(session, role_name),
        email=f"sync-{uuid.uuid4().hex}@example.invalid",
        password_hash=hash_password("sync-test-password"),
        full_name=f"Sync {role_name}",
    )
    session.add(user)
    session.flush()
    role = session.scalar(select(Role).where(Role.name == role_name))
    assert role is not None
    session.add(UserRole(user_id=user.id, role_id=role.id))
    return user


def _project(session, owner: User, *members: tuple[User, str]) -> Project:
    project = Project(name=f"Sync {uuid.uuid4().hex}", owner_id=owner.id, state="ACTIVE")
    session.add(project)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="OFFICER"))
    for user, role in members:
        session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    return project


def _login(client: TestClient, login_id: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"identifier": login_id, "password": "sync-test-password"},
    )
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _parcel(session, project_id: uuid.UUID, initial_version: int = 1) -> Parcel:
    from geoalchemy2.shape import from_shape
    from pyproj import CRS

    poly = Polygon([(77.0, 28.0), (77.01, 28.0), (77.01, 28.01), (77.0, 28.01), (77.0, 28.0)])
    parcel = Parcel(
        project_id=project_id,
        external_identifier=f"PARCEL-{uuid.uuid4().hex[:6]}",
        source="CADASTRAL_GIS",
        source_reference="sync-fixture",
        status="DRAFT",
        current_geometry_version=initial_version,
        coordinate_space="WORLD",
        source_crs="EPSG:4326",
    )
    session.add(parcel)
    session.flush()

    for v in range(1, initial_version + 1):
        version = ParcelGeometryVersion(
            parcel_id=parcel.id,
            version=v,
            geometry=from_shape(poly, srid=4326),
            source_geometry_json=mapping(poly),
            source="HUMAN_DRAWN" if v > 1 else "IMPORT",
            coordinate_space="WORLD",
            source_crs="EPSG:4326",
            area_m2=1000.0,
            area_sqft=10763.9,
            validation_status="VALID",
            created_by_type="HUMAN" if v > 1 else "IMPORT",
        )
        session.add(version)
    session.flush()
    return parcel


def _document_field(session, project_id: uuid.UUID, user_id: uuid.UUID) -> tuple[Document, DocumentExtractedField]:
    source = File(
        project_id=project_id,
        original_name="sync-doc.pdf",
        category="DOCUMENT",
        mime_type="application/pdf",
        size_bytes=100,
        sha256="b" * 64,
        storage_key=f"sync/{uuid.uuid4()}",
        status="UPLOADED",
    )
    session.add(source)
    session.flush()

    document = Document(
        project_id=project_id,
        file_id=source.id,
        uploaded_by_user_id=user_id,
        status="EXTRACTED",
    )
    session.add(document)
    session.flush()

    job = ProcessingJob(
        project_id=project_id,
        job_type="DOCUMENT_AI_PROCESS",
        idempotency_key=f"sync:{uuid.uuid4()}",
        status="COMPLETED",
    )
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

    field = DocumentExtractedField(
        document_id=document.id,
        ocr_result_id=ocr.id,
        candidate_index=0,
        field_name="survey_number",
        original_value="123/4A",
        normalized_value_json="123/4A",
        confidence=0.92,
        page_number=1,
        source_id=str(document.id),
        model_version="test-model-v1",
        extractor_version="test-extractor-v1",
        processed_at=datetime.now(UTC),
    )
    session.add(field)
    session.flush()
    return document, field


def test_repeated_identical_operation_id_is_idempotent() -> None:
    client = TestClient(app)
    with SessionLocal() as session:
        officer = _user(session, "OFFICER")
        surveyor = _user(session, "SURVEYOR")
        project = _project(session, officer, (surveyor, "SURVEYOR"))
        parcel = _parcel(session, project.id, initial_version=1)
        session.commit()
        project_id = str(project.id)
        parcel_id = str(parcel.id)
        surveyor_login = surveyor.login_id

    headers = _login(client, surveyor_login)
    operation_id = str(uuid.uuid4())

    payload = {
        "client_id": "client-mobile-101",
        "operations": [
            {
                "operation_id": operation_id,
                "operation_type": "PARCEL_VERSION_CREATE",
                "entity_id": parcel_id,
                "base_version": 1,
                "payload": {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [77.0, 28.0],
                                [77.012, 28.0],
                                [77.012, 28.012],
                                [77.0, 28.012],
                                [77.0, 28.0],
                            ]
                        ],
                    },
                    "source_crs": "EPSG:4326",
                    "change_reason": "Offline GPS survey correction",
                },
            }
        ],
    }

    # First request -> APPLIED
    res1 = client.post(f"/api/v1/projects/{project_id}/sync/batch", json=payload, headers=headers)
    assert res1.status_code == 200
    body1 = res1.json()
    assert len(body1["results"]) == 1
    assert body1["results"][0]["status"] == "APPLIED"
    assert body1["results"][0]["server_version"] == 2

    # Second request with identical operation_id -> DUPLICATE (never re-increments)
    res2 = client.post(f"/api/v1/projects/{project_id}/sync/batch", json=payload, headers=headers)
    assert res2.status_code == 200
    body2 = res2.json()
    assert len(body2["results"]) == 1
    assert body2["results"][0]["status"] == "DUPLICATE"
    assert body2["results"][0]["server_version"] == 2

    with SessionLocal() as session:
        parcel_db = session.get(Parcel, uuid.UUID(parcel_id))
        assert parcel_db.current_geometry_version == 2
        # Exactly one sync_operations record exists
        ops = list(session.scalars(select(SyncOperation).where(SyncOperation.id == uuid.UUID(operation_id))))
        assert len(ops) == 1
        assert ops[0].status == "APPLIED"


def test_concurrent_parcel_edits_from_same_base_version_triggers_conflict() -> None:
    client = TestClient(app)
    with SessionLocal() as session:
        officer = _user(session, "OFFICER")
        surveyor = _user(session, "SURVEYOR")
        project = _project(session, officer, (surveyor, "SURVEYOR"))
        parcel = _parcel(session, project.id, initial_version=7)
        session.commit()
        project_id = str(project.id)
        parcel_id = str(parcel.id)
        surveyor_login = surveyor.login_id

    headers = _login(client, surveyor_login)

    # First edit from base version 7
    op1_id = str(uuid.uuid4())
    payload1 = {
        "client_id": "client-tablet-a",
        "operations": [
            {
                "operation_id": op1_id,
                "operation_type": "PARCEL_VERSION_CREATE",
                "entity_id": parcel_id,
                "base_version": 7,
                "payload": {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [77.0, 28.0],
                                [77.011, 28.0],
                                [77.011, 28.011],
                                [77.0, 28.011],
                                [77.0, 28.0],
                            ]
                        ],
                    },
                    "source_crs": "EPSG:4326",
                    "change_reason": "Device A update",
                },
            }
        ],
    }

    res1 = client.post(f"/api/v1/projects/{project_id}/sync/batch", json=payload1, headers=headers)
    assert res1.status_code == 200
    assert res1.json()["results"][0]["status"] == "APPLIED"
    assert res1.json()["results"][0]["server_version"] == 8

    # Second edit from Device B also basing on version 7 -> CONFLICT
    op2_id = str(uuid.uuid4())
    payload2 = {
        "client_id": "client-tablet-b",
        "operations": [
            {
                "operation_id": op2_id,
                "operation_type": "PARCEL_VERSION_CREATE",
                "entity_id": parcel_id,
                "base_version": 7,
                "payload": {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [77.0, 28.0],
                                [77.015, 28.0],
                                [77.015, 28.015],
                                [77.0, 28.015],
                                [77.0, 28.0],
                            ]
                        ],
                    },
                    "source_crs": "EPSG:4326",
                    "change_reason": "Device B stale update",
                },
            }
        ],
    }

    res2 = client.post(f"/api/v1/projects/{project_id}/sync/batch", json=payload2, headers=headers)
    assert res2.status_code == 200
    body2 = res2.json()
    assert body2["results"][0]["status"] == "CONFLICT"
    assert body2["results"][0]["conflict"]["conflict_type"] == "VERSION_MISMATCH"
    assert body2["results"][0]["conflict"]["server_version"] == 8
    assert body2["results"][0]["conflict"]["expected_version"] == 7

    # Verify parcel in DB is still version 8 (not overwritten)
    with SessionLocal() as session:
        parcel_db = session.get(Parcel, uuid.UUID(parcel_id))
        assert parcel_db.current_geometry_version == 8


def test_three_item_batch_with_one_invalid_operation_commits_valid_operations() -> None:
    client = TestClient(app)
    with SessionLocal() as session:
        admin = _user(session, "ADMIN")
        project = _project(session, admin)
        parcel1 = _parcel(session, project.id, initial_version=1)
        parcel2 = _parcel(session, project.id, initial_version=1)
        doc, field = _document_field(session, project.id, admin.id)
        session.commit()
        project_id = str(project.id)
        p1_id = str(parcel1.id)
        p2_id = str(parcel2.id)
        field_id = str(field.id)
        admin_login = admin.login_id

    headers = _login(client, admin_login)

    # Batch with:
    # 1. Valid parcel edit (p1) -> APPLIED
    # 2. Bad parcel edit with fake non-existent parcel UUID -> REJECTED
    # 3. Valid field correction -> APPLIED
    op1_id = str(uuid.uuid4())
    op2_id = str(uuid.uuid4())
    op3_id = str(uuid.uuid4())
    fake_uuid = str(uuid.uuid4())

    batch_payload = {
        "client_id": "test-batch-runner",
        "operations": [
            {
                "operation_id": op1_id,
                "operation_type": "PARCEL_VERSION_CREATE",
                "entity_id": p1_id,
                "base_version": 1,
                "payload": {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[77.0, 28.0], [77.01, 28.0], [77.01, 28.01], [77.0, 28.01], [77.0, 28.0]]
                        ],
                    },
                    "source_crs": "EPSG:4326",
                    "change_reason": "Valid p1 adjustment",
                },
            },
            {
                "operation_id": op2_id,
                "operation_type": "PARCEL_VERSION_CREATE",
                "entity_id": fake_uuid,
                "base_version": 1,
                "payload": {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[77.0, 28.0], [77.01, 28.0], [77.01, 28.01], [77.0, 28.01], [77.0, 28.0]]
                        ],
                    },
                    "source_crs": "EPSG:4326",
                },
            },
            {
                "operation_id": op3_id,
                "operation_type": "FIELD_CORRECTION_CREATE",
                "entity_id": field_id,
                "payload": {
                    "corrected_value": "123/4B-CORRECTED",
                    "reason": "Human typo correction in field",
                },
            },
        ],
    }

    res = client.post(f"/api/v1/projects/{project_id}/sync/batch", json=batch_payload, headers=headers)
    assert res.status_code == 200
    results = res.json()["results"]
    assert len(results) == 3

    assert results[0]["operation_id"] == op1_id
    assert results[0]["status"] == "APPLIED"

    assert results[1]["operation_id"] == op2_id
    assert results[1]["status"] == "REJECTED"
    assert results[1]["error"]["code"] == "SYNC_ENTITY_NOT_FOUND"

    assert results[2]["operation_id"] == op3_id
    assert results[2]["status"] == "APPLIED"

    # Verify DB state: p1 incremented to 2, field has correction
    with SessionLocal() as session:
        p1_db = session.get(Parcel, uuid.UUID(p1_id))
        assert p1_db.current_geometry_version == 2


def test_wrong_project_entity_has_strict_isolation() -> None:
    client = TestClient(app)
    with SessionLocal() as session:
        admin1 = _user(session, "ADMIN")
        admin2 = _user(session, "ADMIN")
        project1 = _project(session, admin1)
        project2 = _project(session, admin2)
        parcel2 = _parcel(session, project2.id, initial_version=1)
        session.commit()
        p1_id = str(project1.id)
        p2_parcel_id = str(parcel2.id)
        admin1_login = admin1.login_id

    headers = _login(client, admin1_login)
    op_id = str(uuid.uuid4())

    # User in Project 1 tries to mutate parcel from Project 2
    payload = {
        "operations": [
            {
                "operation_id": op_id,
                "operation_type": "PARCEL_VERSION_CREATE",
                "entity_id": p2_parcel_id,
                "base_version": 1,
                "payload": {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[77.0, 28.0], [77.01, 28.0], [77.01, 28.01], [77.0, 28.01], [77.0, 28.0]]
                        ],
                    },
                    "source_crs": "EPSG:4326",
                },
            }
        ]
    }

    res = client.post(f"/api/v1/projects/{p1_id}/sync/batch", json=payload, headers=headers)
    assert res.status_code == 200
    assert res.json()["results"][0]["status"] == "REJECTED"
    assert res.json()["results"][0]["error"]["code"] == "SYNC_ENTITY_NOT_FOUND"


def test_missing_permission_is_rejected_without_mutation() -> None:
    client = TestClient(app)
    with SessionLocal() as session:
        admin = _user(session, "ADMIN")
        viewer = _user(session, "VIEWER")
        project = _project(session, admin, (viewer, "VIEWER"))
        parcel = _parcel(session, project.id, initial_version=1)
        session.commit()
        project_id = str(project.id)
        parcel_id = str(parcel.id)
        viewer_login = viewer.login_id

    headers = _login(client, viewer_login)
    op_id = str(uuid.uuid4())

    payload = {
        "operations": [
            {
                "operation_id": op_id,
                "operation_type": "PARCEL_VERSION_CREATE",
                "entity_id": parcel_id,
                "base_version": 1,
                "payload": {
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[77.0, 28.0], [77.01, 28.0], [77.01, 28.01], [77.0, 28.01], [77.0, 28.0]]
                        ],
                    },
                    "source_crs": "EPSG:4326",
                },
            }
        ]
    }

    res = client.post(f"/api/v1/projects/{project_id}/sync/batch", json=payload, headers=headers)
    assert res.status_code == 200
    assert res.json()["results"][0]["status"] == "REJECTED"
    assert res.json()["results"][0]["error"]["code"] == "SYNC_PERMISSION_DENIED"

    with SessionLocal() as session:
        parcel_db = session.get(Parcel, uuid.UUID(parcel_id))
        assert parcel_db.current_geometry_version == 1


def test_review_task_changed_while_offline_triggers_conflict() -> None:
    from app.services.review import create_review_task, update_review_task

    client = TestClient(app)
    with SessionLocal() as session:
        reviewer1 = _user(session, "REVIEWER")
        reviewer2 = _user(session, "REVIEWER")
        project = _project(session, reviewer1, (reviewer2, "REVIEWER"))
        task = create_review_task(
            session,
            project_id=project.id,
            queue_type="DOCUMENT",
            target_type="RECORD",
            target_id=uuid.uuid4(),
            severity="LOW",
            summary="Review task test",
            created_by_user_id=reviewer1.id,
        )
        # Another reviewer resolved it online
        update_review_task(
            session,
            task,
            actor=reviewer1,
            action="REJECT",
            assignee_user_id=None,
            reason="Online rejection",
            correction_reference=None,
            reprocess_job_id=None,
        )
        session.commit()
        project_id = str(project.id)
        task_id = str(task.id)
        rev2_login = reviewer2.login_id

    headers = _login(client, rev2_login)
    op_id = str(uuid.uuid4())

    # Reviewer 2 offline queued an APPROVE action
    payload = {
        "operations": [
            {
                "operation_id": op_id,
                "operation_type": "REVIEW_TASK_UPDATE",
                "entity_id": task_id,
                "payload": {
                    "action": "APPROVE",
                    "reason": "Offline approval",
                },
            }
        ]
    }

    res = client.post(f"/api/v1/projects/{project_id}/sync/batch", json=payload, headers=headers)
    assert res.status_code == 200
    result = res.json()["results"][0]
    assert result["status"] == "CONFLICT"
    assert result["conflict"]["conflict_type"] == "STATE_CONFLICT"


def test_change_feed_cursor_pagination_without_duplicates_or_omissions() -> None:
    client = TestClient(app)
    with SessionLocal() as session:
        officer = _user(session, "OFFICER")
        surveyor = _user(session, "SURVEYOR")
        project = _project(session, officer, (surveyor, "SURVEYOR"))
        parcel = _parcel(session, project.id, initial_version=1)
        session.commit()
        project_id = str(project.id)
        parcel_id = str(parcel.id)
        surveyor_login = surveyor.login_id

    headers = _login(client, surveyor_login)

    # Apply 3 sequential mutations
    for i in range(1, 4):
        op_id = str(uuid.uuid4())
        client.post(
            f"/api/v1/projects/{project_id}/sync/batch",
            json={
                "operations": [
                    {
                        "operation_id": op_id,
                        "operation_type": "PARCEL_VERSION_CREATE",
                        "entity_id": parcel_id,
                        "base_version": i,
                        "payload": {
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [
                                    [[77.0, 28.0], [77.01 + (i * 0.001), 28.0], [77.01 + (i * 0.001), 28.01], [77.0, 28.01], [77.0, 28.0]]
                                ],
                            },
                            "source_crs": "EPSG:4326",
                            "change_reason": f"Survey step {i}",
                        },
                    }
                ]
            },
            headers=headers,
        )

    # Fetch changes page by page with limit=2
    res_p1 = client.get(f"/api/v1/projects/{project_id}/sync/changes?cursor=0&limit=2", headers=headers)
    assert res_p1.status_code == 200
    body_p1 = res_p1.json()
    assert len(body_p1["items"]) == 2
    assert body_p1["has_more"] is True
    cursor_1 = body_p1["next_cursor"]

    res_p2 = client.get(f"/api/v1/projects/{project_id}/sync/changes?cursor={cursor_1}&limit=2", headers=headers)
    assert res_p2.status_code == 200
    body_p2 = res_p2.json()
    assert len(body_p2["items"]) == 1
    assert body_p2["has_more"] is False

    # Check that item IDs across pages are distinct and ordered
    all_cursors = [item["cursor"] for item in body_p1["items"]] + [item["cursor"] for item in body_p2["items"]]
    assert len(all_cursors) == len(set(all_cursors))
    assert all_cursors == sorted(all_cursors, key=int)


def test_revoked_or_logged_out_token_is_unauthorized() -> None:
    client = TestClient(app)
    with SessionLocal() as session:
        officer = _user(session, "OFFICER")
        project = _project(session, officer)
        session.commit()
        project_id = str(project.id)
        officer_login = officer.login_id

    headers = _login(client, officer_login)

    # Logout
    logout_res = client.post("/api/v1/auth/logout", headers=headers)
    assert logout_res.status_code == 204

    # Sync request with logged-out session -> 401
    res = client.post(
        f"/api/v1/projects/{project_id}/sync/batch",
        json={"operations": []},
        headers=headers,
    )
    assert res.status_code == 401


def test_batch_size_exceeding_100_returns_422() -> None:
    client = TestClient(app)
    with SessionLocal() as session:
        officer = _user(session, "OFFICER")
        project = _project(session, officer)
        session.commit()
        project_id = str(project.id)
        officer_login = officer.login_id

    headers = _login(client, officer_login)

    large_batch = {
        "operations": [
            {
                "operation_id": str(uuid.uuid4()),
                "operation_type": "PARCEL_VERSION_CREATE",
                "entity_id": str(uuid.uuid4()),
                "base_version": 1,
                "payload": {},
            }
            for _ in range(101)
        ]
    }

    res = client.post(f"/api/v1/projects/{project_id}/sync/batch", json=large_batch, headers=headers)
    assert res.status_code == 422
    assert res.json()["error"]["code"] == "SYNC_BATCH_TOO_LARGE"


def test_sync_operation_recovery_endpoint() -> None:
    client = TestClient(app)
    with SessionLocal() as session:
        officer = _user(session, "OFFICER")
        surveyor = _user(session, "SURVEYOR")
        project = _project(session, officer, (surveyor, "SURVEYOR"))
        parcel = _parcel(session, project.id, initial_version=1)
        session.commit()
        project_id = str(project.id)
        parcel_id = str(parcel.id)
        surveyor_login = surveyor.login_id

    headers = _login(client, surveyor_login)
    op_id = str(uuid.uuid4())

    client.post(
        f"/api/v1/projects/{project_id}/sync/batch",
        json={
            "operations": [
                {
                    "operation_id": op_id,
                    "operation_type": "PARCEL_VERSION_CREATE",
                    "entity_id": parcel_id,
                    "base_version": 1,
                    "payload": {
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [[77.0, 28.0], [77.01, 28.0], [77.01, 28.01], [77.0, 28.01], [77.0, 28.0]]
                            ],
                        },
                        "source_crs": "EPSG:4326",
                    },
                }
            ]
        },
        headers=headers,
    )

    # Recover operation status
    rec_res = client.get(f"/api/v1/projects/{project_id}/sync/operations/{op_id}", headers=headers)
    assert rec_res.status_code == 200
    rec_body = rec_res.json()
    assert rec_body["id"] == op_id
    assert rec_body["status"] == "APPLIED"
    assert rec_body["operation_type"] == "PARCEL_VERSION_CREATE"
    assert rec_body["result"]["parcel_version"] == 2

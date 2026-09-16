import os
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from redis import Redis

from app.core.config import get_settings
from app.core.auth import hash_password
from app.core.database import SessionLocal, engine
from app.core.storage import get_storage_service
from app.main import app
from app.models import Project, ProjectMember, Role, User, UserRole
from app.services.processing_jobs import create_or_get_job

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1",
    reason="Set RUN_DATABASE_TESTS=1 after applying migrations to run database integration tests.",
)


def test_database_connectivity_and_postgis_extension() -> None:
    with engine.connect() as connection:
        assert connection.execute(text("SELECT 1")).scalar_one() == 1
        assert connection.execute(text("SELECT PostGIS_Version()")).scalar_one()


def test_migration_created_foundational_tables_and_seed_data() -> None:
    with engine.connect() as connection:
        assert connection.execute(text("SELECT to_regclass('public.processing_jobs')")).scalar_one() == "processing_jobs"
        assert connection.execute(text("SELECT count(*) FROM roles")).scalar_one() == 5
        assert connection.execute(text("SELECT count(*) FROM permissions")).scalar_one() == 28


def test_redis_and_private_minio_are_available() -> None:
    assert Redis.from_url(get_settings().redis_url).ping() is True
    storage = get_storage_service()
    storage.ensure_private_bucket()
    storage.client.head_bucket(Bucket=storage.bucket)


def test_private_presigned_upload_completion_and_download_flow() -> None:
    unique_suffix = uuid.uuid4().hex
    with SessionLocal() as session:
        user = User(
            email=f"phase-b2-{unique_suffix}@example.invalid",
            password_hash=hash_password("phase-b2-test-password"),
            full_name="Phase B2 Test",
        )
        session.add(user)
        session.flush()
        project = Project(name=f"Phase B2 {unique_suffix}", owner_id=user.id, state="ACTIVE")
        session.add(project)
        session.flush()
        officer_role = session.scalar(select(Role).where(Role.name == "OFFICER"))
        assert officer_role is not None
        session.add(UserRole(user_id=user.id, role_id=officer_role.id))
        session.add(ProjectMember(project_id=project.id, user_id=user.id, role="OFFICER"))
        session.commit()
        project_id = project.id

    client = TestClient(app)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": user.email, "password": "phase-b2-test-password"},
    )
    assert login.status_code == 200
    authorization = {"Authorization": f"Bearer {login.json()['access_token']}"}
    presign = client.post(
        "/api/v1/files/presign",
        json={
            "project_id": str(project_id),
            "filename": "private-record.pdf",
            "content_type": "application/pdf",
            "size_bytes": 9,
            "category": "DOCUMENT",
            "sha256": "b" * 64,
        },
        headers=authorization,
    )
    assert presign.status_code == 201
    presign_body = presign.json()
    assert "localhost" not in presign_body["upload_url"]

    upload = httpx.put(
        presign_body["upload_url"],
        content=b"phase-b2!",
        headers=presign_body["required_headers"],
    )
    assert upload.status_code == 200

    complete = client.post(
        "/api/v1/files/complete",
        json={"file_id": presign_body["file_id"]},
        headers=authorization,
    )
    assert complete.status_code == 200
    assert complete.json()["status"] == "UPLOADED"

    download = client.get(f"/api/v1/files/{presign_body['file_id']}/download", headers=authorization)
    assert download.status_code == 200
    assert "localhost" not in download.json()["download_url"]
    assert httpx.get(download.json()["download_url"]).content == b"phase-b2!"


def test_processing_job_idempotency_returns_the_existing_job() -> None:
    unique_suffix = uuid.uuid4().hex
    with SessionLocal() as session:
        user = User(
            email=f"phase-b2-idempotency-{unique_suffix}@example.invalid",
            password_hash="not-a-password",
            full_name="Phase B2 Idempotency Test",
        )
        session.add(user)
        session.flush()
        project = Project(name=f"Phase B2 Idempotency {unique_suffix}", owner_id=user.id, state="ACTIVE")
        session.add(project)
        session.flush()

        first, first_created = create_or_get_job(session, project.id, "FILE_REGISTERED", "repeat-key")
        session.commit()
        second, second_created = create_or_get_job(session, project.id, "FILE_REGISTERED", "repeat-key")

        assert first_created is True
        assert second_created is False
        assert first.id == second.id

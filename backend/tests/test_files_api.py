import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.api.v1.files import get_db_session
from app.core.storage import ObjectInfo, StorageObjectNotFoundError, get_storage_service
from app.main import app
from app.models import File, ProcessingJob, Project, ProjectMember, User


class FakeSession:
    def __init__(self, project: Project | None = None, file: File | None = None, user: User | None = None) -> None:
        self.project = project
        self.file = file
        self.user = user or User(
            id=uuid.uuid4(),
            login_id="OFF-TN-000001",
            email="officer@example.invalid",
            password_hash="hash",
            full_name="Officer",
        )
        self.added: list[object] = []
        self.commit_count = 0

    def get(self, model, identifier):
        if model is Project:
            return self.project if self.project and self.project.id == identifier else None
        if model is File:
            return self.file if self.file and self.file.id == identifier else None
        if model is ProjectMember and isinstance(identifier, dict):
            if self.project and identifier["project_id"] == self.project.id and identifier["user_id"] == self.user.id:
                return ProjectMember(project_id=self.project.id, user_id=self.user.id, role="OFFICER")
            if self.file and identifier["project_id"] == self.file.project_id and identifier["user_id"] == self.user.id:
                return ProjectMember(project_id=self.file.project_id, user_id=self.user.id, role="OFFICER")
        return None

    def add(self, item) -> None:
        self.added.append(item)
        if isinstance(item, File):
            self.file = item

    def flush(self) -> None:
        for item in self.added:
            if isinstance(item, ProcessingJob) and item.id is None:
                item.id = uuid.uuid4()
        return None

    def scalar(self, _statement):
        return None

    def scalars(self, _statement):
        return iter(["document:upload", "imagery:upload", "document:read"])

    def commit(self) -> None:
        self.commit_count += 1

    def rollback(self) -> None:
        return None


class FakeStorage:
    def generate_storage_key(self, project_id, file_id, filename) -> str:
        return f"projects/{project_id}/originals/{file_id}/{filename}"

    def presign_upload(self, _storage_key, content_type, checksum):
        headers = {"Content-Type": content_type}
        if checksum:
            headers["x-amz-meta-sha256"] = checksum
        return "https://signed.example/upload", headers

    def get_object_info(self, _storage_key) -> ObjectInfo:
        return ObjectInfo(1024, "application/pdf", {"sha256": "a" * 64})

    def presign_download(self, _storage_key) -> str:
        return "https://signed.example/download"


@pytest.fixture(autouse=True)
def clean_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def authorize_test_user(session: FakeSession) -> None:
    app.dependency_overrides[get_current_user] = lambda: session.user


def test_presign_creates_pending_file_with_server_key() -> None:
    project = Project(id=uuid.uuid4(), name="Project", owner_id=uuid.uuid4(), state="ACTIVE")
    session = FakeSession(project=project)
    app.dependency_overrides[get_db_session] = lambda: session
    app.dependency_overrides[get_storage_service] = lambda: FakeStorage()
    authorize_test_user(session)

    response = TestClient(app).post(
        "/api/v1/files/presign",
        json={
            "project_id": str(project.id),
            "filename": "record.pdf",
            "content_type": "application/pdf",
            "size_bytes": 1024,
            "category": "DOCUMENT",
            "sha256": "a" * 64,
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "PENDING_UPLOAD"
    assert response.json()["upload_url"] == "https://signed.example/upload"
    assert session.file is not None
    assert session.file.storage_key.startswith(f"projects/{project.id}/originals/")


def test_complete_registers_file_and_creates_processing_job(monkeypatch) -> None:
    file = File(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        original_name="record.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        sha256="a" * 64,
        storage_key="projects/a/originals/b/record.pdf",
        status="PENDING_UPLOAD",
    )
    session = FakeSession(project=Project(id=file.project_id, name="Project", owner_id=uuid.uuid4(), state="ACTIVE"), file=file)
    app.dependency_overrides[get_db_session] = lambda: session
    app.dependency_overrides[get_storage_service] = lambda: FakeStorage()
    authorize_test_user(session)
    monkeypatch.setattr("app.workers.tasks.process_file_registration.delay", lambda _job_id: None)

    response = TestClient(app).post("/api/v1/files/complete", json={"file_id": str(file.id)})

    assert response.status_code == 200
    assert response.json()["status"] == "UPLOADED"
    assert file.status == "UPLOADED"
    assert any(item.__class__.__name__ == "ProcessingJob" for item in session.added)


def test_download_rejects_invalid_or_incomplete_file_ids() -> None:
    session = FakeSession()
    app.dependency_overrides[get_db_session] = lambda: session
    app.dependency_overrides[get_storage_service] = lambda: FakeStorage()
    authorize_test_user(session)

    missing = TestClient(app).get(f"/api/v1/files/{uuid.uuid4()}/download")

    assert missing.status_code == 404

    pending = File(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        original_name="record.pdf",
        mime_type="application/pdf",
        size_bytes=1024,
        storage_key="projects/a/originals/b/record.pdf",
        status="PENDING_UPLOAD",
    )
    session.file = pending
    session.project = Project(id=pending.project_id, name="Project", owner_id=uuid.uuid4(), state="ACTIVE")
    unavailable = TestClient(app).get(f"/api/v1/files/{pending.id}/download")
    assert unavailable.status_code == 409

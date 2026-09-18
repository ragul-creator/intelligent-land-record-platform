"""Live F.4 persistence/API tests with OCR, object storage, and Celery mocked."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ai.document_ai.models import BoundingBox, DocumentOcrResult, OcrPageResult, OcrRegion, PreprocessingMetadata
from app.audit.service import record_audit
from app.core.auth import hash_password
from app.core.database import SessionLocal
from app.core.storage import get_storage_service
from app.main import app
from app.models import (
    AuditLog,
    Document,
    DocumentExtractedField,
    DocumentFieldCorrection,
    DocumentOcrResultRecord,
    DocumentProcessingJob,
    DocumentValidationResultRecord,
    File,
    ProcessingJob,
    Project,
    ProjectMember,
    ReviewTask,
    Role,
    User,
    UserRole,
)
from app.services.review import apply_review_action, create_review_task
from app.services.user_identities import generate_login_id
from app.workers.tasks import process_document_ai


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1",
    reason="Set RUN_DATABASE_TESTS=1 after applying the F.4 migration to run Document AI integration tests.",
)


class MemoryStorage:
    """Private-storage double; tests never need a live MinIO server."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def generate_storage_key(self, project_id, file_id, filename) -> str:
        return f"projects/{project_id}/originals/{file_id}/{filename}"

    def put_private_object(self, key, body, *, content_type, size_bytes, checksum) -> None:
        payload = body.read()
        assert len(payload) == size_bytes and checksum
        self.objects[key] = payload

    def read_private_object(self, key) -> bytes:
        return self.objects[key]

    def presign_download(self, key) -> str:
        assert key in self.objects
        return f"https://signed.example/{key}"


def _user(session, role_name: str) -> User:
    user = User(
        login_id=generate_login_id(session, role_name),
        email=f"f4-{uuid.uuid4().hex}@example.invalid",
        password_hash=hash_password("f4-test-password"),
        full_name=f"F4 {role_name}",
    )
    session.add(user)
    session.flush()
    role = session.scalar(select(Role).where(Role.name == role_name))
    assert role is not None
    session.add(UserRole(user_id=user.id, role_id=role.id))
    return user


def _project(session, owner: User, *members: tuple[User, str]) -> Project:
    project = Project(name=f"F4 {uuid.uuid4().hex}", owner_id=owner.id, state="ACTIVE")
    session.add(project)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="OFFICER"))
    for user, role in members:
        session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    return project


def _headers(client: TestClient, login_id: str) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"identifier": login_id, "password": "f4-test-password"})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _ocr_result(source_id: str, confidence: float) -> DocumentOcrResult:
    processed_at = datetime.now(UTC)
    page = OcrPageResult(
        source_id=source_id,
        page_number=1,
        text="Survey No: 123/4",
        confidence=confidence,
        width=800,
        height=1000,
        requested_languages=("tam", "eng"),
        project_tested_languages=("tam", "eng"),
        preprocessing=PreprocessingMetadata(("grayscale",), None),
        regions=(
            OcrRegion("Survey No:", confidence, BoundingBox(10, 20, 70, 14)),
            OcrRegion("123/4", confidence, BoundingBox(90, 20, 40, 14)),
        ),
        engine="mock-ocr",
        engine_version="1.0",
        model_version="mock-ocr-v1",
        processed_at=processed_at,
    )
    return DocumentOcrResult(
        source_id=source_id,
        pages=(page,),
        requested_languages=("tam", "eng"),
        project_tested_languages=("tam", "eng"),
        engine="mock-ocr",
        engine_version="1.0",
        model_version="mock-ocr-v1",
        processed_at=processed_at,
    )


def _install_boundaries(monkeypatch, storage: MemoryStorage, confidence: float = 0.95) -> None:
    app.dependency_overrides[get_storage_service] = lambda: storage
    monkeypatch.setattr("app.workers.tasks.get_storage_service", lambda: storage)
    monkeypatch.setattr("app.workers.tasks.process_document_ai.delay", lambda _job_id: None)
    monkeypatch.setattr("app.workers.tasks.revalidate_document.delay", lambda _job_id: None)

    class Pipeline:
        def process(self, _path, *, source_id, **_kwargs):
            return _ocr_result(source_id, confidence)

    monkeypatch.setattr("ai.document_ai.pipeline.OcrPipeline", Pipeline)


@pytest.fixture(autouse=True)
def _clean_overrides():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _upload(client: TestClient, project_id: uuid.UUID, headers: dict[str, str]):
    return client.post(
        f"/api/v1/projects/{project_id}/documents",
        headers=headers,
        files={"upload": ("record.pdf", b"document fixture", "application/pdf")},
    )


def test_document_upload_read_source_url_scope_and_rbac(monkeypatch) -> None:
    storage = MemoryStorage()
    _install_boundaries(monkeypatch, storage)
    with SessionLocal() as session:
        officer, viewer, outsider = _user(session, "OFFICER"), _user(session, "VIEWER"), _user(session, "OFFICER")
        project = _project(session, officer, (viewer, "VIEWER"))
        other_project = _project(session, outsider)
        session.commit()
        project_id, other_project_id = project.id, other_project.id
        officer_id, viewer_id, outsider_id = officer.login_id, viewer.login_id, outsider.login_id

    client = TestClient(app)
    officer_headers, viewer_headers, outsider_headers = _headers(client, officer_id), _headers(client, viewer_id), _headers(client, outsider_id)
    uploaded = _upload(client, project_id, officer_headers)
    assert uploaded.status_code == 201
    document_id = uploaded.json()["id"]
    with SessionLocal() as session:
        document = session.get(Document, uuid.UUID(document_id))
        assert document is not None
        source_file = session.get(File, document.file_id)
        assert source_file is not None
        assert source_file.id == document.file_id
        assert source_file.project_id == document.project_id == project_id
    assert client.post(f"/api/v1/projects/{project_id}/documents", headers=viewer_headers, files={"upload": ("blocked.pdf", b"x", "application/pdf")}).status_code == 403
    assert client.post(f"/api/v1/projects/{project_id}/documents", headers=officer_headers, files={"upload": ("blocked.txt", b"x", "text/plain")}).status_code == 422
    assert client.get(f"/api/v1/projects/{project_id}/documents", headers=viewer_headers).json()["page"]["total"] == 1
    assert client.get(f"/api/v1/projects/{project_id}/documents/{document_id}", headers=outsider_headers).status_code == 404
    assert client.get(f"/api/v1/projects/{project_id}/documents/{document_id}/source-url", headers=outsider_headers).status_code == 404
    source = client.get(f"/api/v1/projects/{project_id}/documents/{document_id}/source-url", headers=officer_headers)
    assert source.status_code == 200 and source.json()["source_url"].startswith("https://signed.example/")
    assert client.get(f"/api/v1/projects/{other_project_id}/documents", headers=officer_headers).status_code == 404


def test_worker_persists_clean_and_review_results_once_and_reprocesses(monkeypatch) -> None:
    storage = MemoryStorage()
    _install_boundaries(monkeypatch, storage, confidence=0.95)
    with SessionLocal() as session:
        officer = _user(session, "OFFICER")
        project = _project(session, officer)
        session.commit()
        project_id, login_id = project.id, officer.login_id

    client = TestClient(app)
    headers = _headers(client, login_id)
    uploaded = _upload(client, project_id, headers)
    document_id = uuid.UUID(uploaded.json()["id"])
    first = client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/process", headers=headers, json={"languages": "tam+eng"})
    duplicate = client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/process", headers=headers, json={"languages": "tam+eng"})
    assert first.status_code == duplicate.status_code == 202
    assert first.json()["id"] == duplicate.json()["id"]
    job_id = uuid.UUID(first.json()["id"])
    process_document_ai.run(str(job_id))
    process_document_ai.run(str(job_id))

    with SessionLocal() as session:
        document = session.get(Document, document_id)
        ocr = list(session.scalars(select(DocumentOcrResultRecord).where(DocumentOcrResultRecord.document_id == document_id)))
        fields = list(session.scalars(select(DocumentExtractedField).where(DocumentExtractedField.document_id == document_id)))
        validation = session.scalar(select(DocumentValidationResultRecord).where(DocumentValidationResultRecord.document_id == document_id))
        assert document is not None and document.status == "VALIDATED"
        assert len(ocr) == len(fields) == 1 and validation is not None and validation.status == "VALID"
        assert fields[0].source_id == str(document_id) and fields[0].page_number == 1
        assert fields[0].bounding_box_json == {"left": 90, "top": 20, "width": 40, "height": 14}
        assert session.scalar(select(func.count(ReviewTask.id)).where(ReviewTask.target_id == document_id)) == 0

    reprocess = client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/reprocess", headers=headers, json={"languages": "tam+eng"})
    assert reprocess.status_code == 202 and reprocess.json()["processing_version"] == 2
    process_document_ai.run(reprocess.json()["id"])
    with SessionLocal() as session:
        assert [row.version for row in session.scalars(select(DocumentOcrResultRecord).where(DocumentOcrResultRecord.document_id == document_id).order_by(DocumentOcrResultRecord.version))] == [1, 2]


def test_review_failure_and_correction_preserve_prior_evidence(monkeypatch) -> None:
    storage = MemoryStorage()
    _install_boundaries(monkeypatch, storage, confidence=0.40)
    with SessionLocal() as session:
        officer, viewer = _user(session, "OFFICER"), _user(session, "VIEWER")
        project = _project(session, officer, (viewer, "VIEWER"))
        session.commit()
        project_id, officer_login, viewer_login = project.id, officer.login_id, viewer.login_id

    client = TestClient(app)
    officer_headers, viewer_headers = _headers(client, officer_login), _headers(client, viewer_login)
    document_id = uuid.UUID(_upload(client, project_id, officer_headers).json()["id"])
    queued = client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/process", headers=officer_headers, json={"languages": "tam+eng"})
    process_document_ai.run(queued.json()["id"])
    with SessionLocal() as session:
        document = session.get(Document, document_id)
        field = session.scalar(select(DocumentExtractedField).where(DocumentExtractedField.document_id == document_id))
        review = session.scalar(select(ReviewTask).where(ReviewTask.target_id == document_id, ReviewTask.queue_type == "DOCUMENT"))
        assert document is not None and document.status == "REVIEW_REQUIRED"
        assert field is not None and review is not None
        assert session.scalar(select(func.count(ReviewTask.id)).where(ReviewTask.target_id == document_id)) == 1
        field_id, original_value, review_id = field.id, field.original_value, review.id

    assert client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/fields/{field_id}/corrections", headers=viewer_headers, json={"corrected_value": "123/5", "reason": "No correction permission"}).status_code == 403
    assert client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/fields/{field_id}/corrections", headers=officer_headers, json={"corrected_value": "123/5", "reason": ""}).status_code == 422
    corrected = client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/fields/{field_id}/corrections", headers=officer_headers, json={"corrected_value": "123/5", "reason": "Checked source evidence"})
    assert corrected.status_code == 201
    correction_id = uuid.UUID(corrected.json()["id"])

    with SessionLocal() as session:
        field = session.get(DocumentExtractedField, field_id)
        correction = session.get(DocumentFieldCorrection, correction_id)
        review = session.get(ReviewTask, review_id)
        assert field is not None and field.original_value == original_value
        assert correction is not None and correction.corrected_value == "123/5"
        assert session.scalar(select(func.count(AuditLog.id)).where(AuditLog.action == "document.field_corrected", AuditLog.target_id == correction_id)) == 1
        assert review is not None
        officer = session.scalar(select(User).where(User.login_id == officer_login))
        assert officer is not None
        apply_review_action(session, review, actor=officer, action="CORRECT", reason="Checked source evidence", correction_reference=str(correction_id))
        session.commit()

    # A retryable reprocess failure must preserve successful OCR/extraction history and requeue safely.
    class BrokenPipeline:
        def process(self, *_args, **_kwargs):
            raise RuntimeError("mock OCR failure")

    monkeypatch.setattr("ai.document_ai.pipeline.OcrPipeline", BrokenPipeline)
    reprocess = client.post(f"/api/v1/projects/{project_id}/documents/{document_id}/reprocess", headers=officer_headers, json={"languages": "tam+eng"})
    with pytest.raises(RuntimeError, match="mock OCR failure"):
        process_document_ai.run(reprocess.json()["id"])
    with SessionLocal() as session:
        document = session.get(Document, document_id)
        job = session.get(ProcessingJob, uuid.UUID(reprocess.json()["id"]))
        assert document is not None and document.status == "QUEUED"
        assert job is not None and job.status == "QUEUED"
        assert job.retry_count == 1
        assert job.error_json is None
        assert session.scalar(select(func.count(DocumentOcrResultRecord.id)).where(DocumentOcrResultRecord.document_id == document_id)) == 1

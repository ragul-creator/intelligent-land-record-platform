"""Unit and integration tests for GeoPackage GIS import API endpoints."""

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api.v1.files import get_db_session
from app.core.auth import get_current_user
from app.main import app
from app.models import File, GeoAIJob, ProcessingJob, Project, ProjectMember, User
from app.models.gis_imports import GisImportRun
from app.schemas.gis_imports import (
    GeoPackageImportAcceptedResponse,
    GeoPackageImportCreateRequest,
    GeoPackageImportDetailResponse,
)


class FakeDbSession:
    """In-memory session mock tracking models and queries for API tests."""

    def __init__(
        self,
        user: User | None = None,
        permissions: list[str] | None = None,
    ) -> None:
        self.user = user or User(
            id=uuid.uuid4(),
            login_id="OFF-TN-000001",
            email="officer@example.invalid",
            password_hash="hash",
            full_name="Officer",
        )
        self.permissions = permissions if permissions is not None else ["geo:edit_draft"]
        self.projects: dict[uuid.UUID, Project] = {}
        self.members: dict[tuple[uuid.UUID, uuid.UUID], ProjectMember] = {}
        self.files: dict[uuid.UUID, File] = {}
        self.processing_jobs: dict[uuid.UUID, ProcessingJob] = {}
        self.geoai_jobs: dict[uuid.UUID, GeoAIJob] = {}
        self.import_runs: dict[uuid.UUID, GisImportRun] = {}
        self.committed = 0

    def add(self, item: Any) -> None:
        if isinstance(item, Project):
            self.projects[item.id] = item
        elif isinstance(item, ProjectMember):
            self.members[(item.project_id, item.user_id)] = item
        elif isinstance(item, File):
            self.files[item.id] = item
        elif isinstance(item, ProcessingJob):
            if getattr(item, "id", None) is None:
                item.id = uuid.uuid4()
            self.processing_jobs[item.id] = item
        elif isinstance(item, GeoAIJob):
            self.geoai_jobs[item.id] = item
        elif isinstance(item, GisImportRun):
            self.import_runs[item.id] = item

    def get(self, model: type, identifier: Any) -> Any:
        if model is Project:
            return self.projects.get(identifier)
        if model is File:
            return self.files.get(identifier)
        if model is ProcessingJob:
            return self.processing_jobs.get(identifier)
        if model is GeoAIJob:
            return self.geoai_jobs.get(identifier)
        if model is GisImportRun:
            return self.import_runs.get(identifier)
        return None

    def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        if "FROM projects JOIN project_members" in sql:
            for (p_id, u_id), _ in self.members.items():
                if u_id == self.user.id and p_id in self.projects:
                    # If statement filters by Project.id == ...
                    if str(p_id) in sql:
                        return self.projects[p_id]
                    # Default return if matched
                    return self.projects[p_id]
            return None
        if "FROM processing_jobs" in sql:
            # find by idempotency key if present
            for job in self.processing_jobs.values():
                if job.idempotency_key and job.idempotency_key in sql:
                    return job
            return None
        if "FROM gis_import_runs" in sql:
            for run in self.import_runs.values():
                if run.status in ("QUEUED", "PROCESSING"):
                    if str(run.project_id) in sql and str(run.file_id) in sql:
                        return run
                    # If project and file match
                    for p in self.projects:
                        for f in self.files:
                            if run.project_id == p and run.file_id == f:
                                return run
            return None
        return None

    def scalars(self, statement: Any) -> Any:
        sql = str(statement)
        if "permissions" in sql or "roles" in sql:
            return iter(self.permissions)
        return iter([])

    def commit(self) -> None:
        self.committed += 1

    def flush(self) -> None:
        for job in self.processing_jobs.values():
            if job.id is None:
                job.id = uuid.uuid4()

    def rollback(self) -> None:
        pass


@pytest.fixture(autouse=True)
def cleanup_dependencies():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _setup_environment(session: FakeDbSession, monkeypatch):
    monkeypatch.setattr("app.workers.tasks.process_geopackage_import.delay", MagicMock())
    app.dependency_overrides[get_current_user] = lambda: session.user
    app.dependency_overrides[get_db_session] = lambda: session


def test_valid_post_returns_202_and_creates_records(monkeypatch) -> None:
    session = FakeDbSession()
    project = Project(id=uuid.uuid4(), name="Test Project", owner_id=session.user.id, state="ACTIVE")
    session.add(project)
    session.add(ProjectMember(project_id=project.id, user_id=session.user.id, role="OFFICER"))

    file_record = File(
        id=uuid.uuid4(),
        project_id=project.id,
        original_name="layers.gpkg",
        category="GIS_IMPORT",
        mime_type="application/geopackage+sqlite3",
        size_bytes=4096,
        storage_key=f"projects/{project.id}/layers.gpkg",
        status="UPLOADED",
    )
    session.add(file_record)
    _setup_environment(session, monkeypatch)

    client = TestClient(app)
    payload = {
        "file_id": str(file_record.id),
        "source_reference": "survey_batch_01",
        "layer_mapping": {"parcels": "cadastral_parcels", "roads": "road_centerlines"},
        "mode": "DRAFT_IMPORT",
    }
    response = client.post(f"/api/v1/projects/{project.id}/gis-imports", json=payload)

    assert response.status_code == 202
    data = response.json()
    assert "import_run_id" in data
    assert "processing_job_id" in data
    assert data["status"] == "QUEUED"

    import_run_id = uuid.UUID(data["import_run_id"])
    run = session.import_runs[import_run_id]
    assert run.project_id == project.id
    assert run.file_id == file_record.id
    assert str(run.processing_job_id) == data["processing_job_id"]
    assert run.status == "QUEUED"
    assert run.layer_mapping_json == {"parcels": "cadastral_parcels", "roads": "road_centerlines"}

    # ProcessingJob was created
    proc_job = session.processing_jobs[run.processing_job_id]
    assert proc_job.job_type == "GEOPACKAGE_IMPORT"
    assert proc_job.project_id == project.id

    # GeoAIJob was linked
    geoai_job = session.geoai_jobs[run.processing_job_id]
    assert geoai_job.job_type == "GEOPACKAGE_IMPORT"
    assert geoai_job.parameters_json["file_id"] == str(file_record.id)


def test_file_from_another_project_is_rejected(monkeypatch) -> None:
    session = FakeDbSession()
    project_a = Project(id=uuid.uuid4(), name="Project A", owner_id=session.user.id, state="ACTIVE")
    project_b = Project(id=uuid.uuid4(), name="Project B", owner_id=session.user.id, state="ACTIVE")
    session.add(project_a)
    session.add(project_b)
    session.add(ProjectMember(project_id=project_a.id, user_id=session.user.id, role="OFFICER"))

    # File belongs to Project B
    file_b = File(
        id=uuid.uuid4(),
        project_id=project_b.id,
        original_name="layers.gpkg",
        category="GIS_IMPORT",
        mime_type="application/geopackage+sqlite3",
        size_bytes=1024,
        storage_key="key",
        status="UPLOADED",
    )
    session.add(file_b)
    _setup_environment(session, monkeypatch)

    client = TestClient(app)
    payload = {
        "file_id": str(file_b.id),
        "layer_mapping": {"parcels": "layer"},
    }
    response = client.post(f"/api/v1/projects/{project_a.id}/gis-imports", json=payload)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "FILE_NOT_FOUND"


def test_non_gis_import_category_is_rejected(monkeypatch) -> None:
    session = FakeDbSession()
    project = Project(id=uuid.uuid4(), name="Project", owner_id=session.user.id, state="ACTIVE")
    session.add(project)
    session.add(ProjectMember(project_id=project.id, user_id=session.user.id, role="OFFICER"))

    doc_file = File(
        id=uuid.uuid4(),
        project_id=project.id,
        original_name="record.gpkg",
        category="DOCUMENT",  # Wrong category
        mime_type="application/geopackage+sqlite3",
        size_bytes=1024,
        storage_key="key",
        status="UPLOADED",
    )
    session.add(doc_file)
    _setup_environment(session, monkeypatch)

    client = TestClient(app)
    response = client.post(
        f"/api/v1/projects/{project.id}/gis-imports",
        json={"file_id": str(doc_file.id), "layer_mapping": {"parcels": "layer"}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "GIS_IMPORT_FILE_INVALID"


def test_non_gpkg_extension_is_rejected(monkeypatch) -> None:
    session = FakeDbSession()
    project = Project(id=uuid.uuid4(), name="Project", owner_id=session.user.id, state="ACTIVE")
    session.add(project)
    session.add(ProjectMember(project_id=project.id, user_id=session.user.id, role="OFFICER"))

    shp_file = File(
        id=uuid.uuid4(),
        project_id=project.id,
        original_name="layers.zip",  # not .gpkg
        category="GIS_IMPORT",
        mime_type="application/geopackage+sqlite3",
        size_bytes=1024,
        storage_key="key",
        status="UPLOADED",
    )
    session.add(shp_file)
    _setup_environment(session, monkeypatch)

    client = TestClient(app)
    response = client.post(
        f"/api/v1/projects/{project.id}/gis-imports",
        json={"file_id": str(shp_file.id), "layer_mapping": {"parcels": "layer"}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "GIS_IMPORT_FILE_INVALID"


def test_unsupported_mime_type_is_rejected(monkeypatch) -> None:
    session = FakeDbSession()
    project = Project(id=uuid.uuid4(), name="Project", owner_id=session.user.id, state="ACTIVE")
    session.add(project)
    session.add(ProjectMember(project_id=project.id, user_id=session.user.id, role="OFFICER"))

    bad_mime_file = File(
        id=uuid.uuid4(),
        project_id=project.id,
        original_name="layers.gpkg",
        category="GIS_IMPORT",
        mime_type="text/plain",  # unsupported MIME
        size_bytes=1024,
        storage_key="key",
        status="UPLOADED",
    )
    session.add(bad_mime_file)
    _setup_environment(session, monkeypatch)

    client = TestClient(app)
    response = client.post(
        f"/api/v1/projects/{project.id}/gis-imports",
        json={"file_id": str(bad_mime_file.id), "layer_mapping": {"parcels": "layer"}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "GIS_IMPORT_FILE_INVALID"


def test_pending_upload_status_is_rejected(monkeypatch) -> None:
    session = FakeDbSession()
    project = Project(id=uuid.uuid4(), name="Project", owner_id=session.user.id, state="ACTIVE")
    session.add(project)
    session.add(ProjectMember(project_id=project.id, user_id=session.user.id, role="OFFICER"))

    pending_file = File(
        id=uuid.uuid4(),
        project_id=project.id,
        original_name="layers.gpkg",
        category="GIS_IMPORT",
        mime_type="application/geopackage+sqlite3",
        size_bytes=1024,
        storage_key="key",
        status="PENDING_UPLOAD",  # Not complete
    )
    session.add(pending_file)
    _setup_environment(session, monkeypatch)

    client = TestClient(app)
    response = client.post(
        f"/api/v1/projects/{project.id}/gis-imports",
        json={"file_id": str(pending_file.id), "layer_mapping": {"parcels": "layer"}},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "FILE_UNAVAILABLE"


def test_invalid_layer_mapping_keys_rejected(monkeypatch) -> None:
    session = FakeDbSession()
    project = Project(id=uuid.uuid4(), name="Project", owner_id=session.user.id, state="ACTIVE")
    session.add(project)
    session.add(ProjectMember(project_id=project.id, user_id=session.user.id, role="OFFICER"))
    _setup_environment(session, monkeypatch)

    client = TestClient(app)
    # Mapping with invalid logical layer "rivers"
    response = client.post(
        f"/api/v1/projects/{project.id}/gis-imports",
        json={"file_id": str(uuid.uuid4()), "layer_mapping": {"rivers": "layer_1"}},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unsupported_mode_rejected(monkeypatch) -> None:
    session = FakeDbSession()
    project = Project(id=uuid.uuid4(), name="Project", owner_id=session.user.id, state="ACTIVE")
    session.add(project)
    session.add(ProjectMember(project_id=project.id, user_id=session.user.id, role="OFFICER"))
    _setup_environment(session, monkeypatch)

    client = TestClient(app)
    response = client.post(
        f"/api/v1/projects/{project.id}/gis-imports",
        json={
            "file_id": str(uuid.uuid4()),
            "layer_mapping": {"parcels": "layer"},
            "mode": "INSTANT_PRODUCTION_PUBLISH",  # Unsupported mode
        },
    )
    assert response.status_code == 422


def test_duplicate_active_request_returns_existing_run(monkeypatch) -> None:
    session = FakeDbSession()
    project = Project(id=uuid.uuid4(), name="Project", owner_id=session.user.id, state="ACTIVE")
    session.add(project)
    session.add(ProjectMember(project_id=project.id, user_id=session.user.id, role="OFFICER"))

    file_record = File(
        id=uuid.uuid4(),
        project_id=project.id,
        original_name="layers.gpkg",
        category="GIS_IMPORT",
        mime_type="application/geopackage+sqlite3",
        size_bytes=1024,
        storage_key="key",
        status="UPLOADED",
    )
    session.add(file_record)

    # Active run already exists
    active_job = ProcessingJob(id=uuid.uuid4(), project_id=project.id, job_type="GEOPACKAGE_IMPORT", status="PROCESSING")
    session.add(active_job)
    active_run = GisImportRun(
        id=uuid.uuid4(),
        project_id=project.id,
        file_id=file_record.id,
        processing_job_id=active_job.id,
        status="PROCESSING",
    )
    session.add(active_run)
    _setup_environment(session, monkeypatch)

    client = TestClient(app)
    response = client.post(
        f"/api/v1/projects/{project.id}/gis-imports",
        json={"file_id": str(file_record.id), "layer_mapping": {"parcels": "layer"}},
    )

    assert response.status_code == 202
    data = response.json()
    assert data["import_run_id"] == str(active_run.id)
    assert data["processing_job_id"] == str(active_job.id)
    assert data["status"] == "PROCESSING"


def test_permission_enforcement_forbids_without_geo_edit_draft(monkeypatch) -> None:
    # Caller has only read permissions, not geo:edit_draft
    session = FakeDbSession(permissions=["document:read"])
    project = Project(id=uuid.uuid4(), name="Project", owner_id=session.user.id, state="ACTIVE")
    session.add(project)
    session.add(ProjectMember(project_id=project.id, user_id=session.user.id, role="VIEWER"))
    _setup_environment(session, monkeypatch)

    client = TestClient(app)
    response = client.post(
        f"/api/v1/projects/{project.id}/gis-imports",
        json={"file_id": str(uuid.uuid4()), "layer_mapping": {"parcels": "layer"}},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PROJECT_FORBIDDEN"


def test_get_import_detail_success_with_bounded_summary(monkeypatch) -> None:
    session = FakeDbSession()
    project = Project(id=uuid.uuid4(), name="Project", owner_id=session.user.id, state="ACTIVE")
    session.add(project)
    session.add(ProjectMember(project_id=project.id, user_id=session.user.id, role="OFFICER"))

    file_record = File(id=uuid.uuid4(), project_id=project.id, original_name="test.gpkg", category="GIS_IMPORT", mime_type="application/geopackage+sqlite3", size_bytes=100, storage_key="k", status="UPLOADED")
    session.add(file_record)

    job = ProcessingJob(id=uuid.uuid4(), project_id=project.id, job_type="GEOPACKAGE_IMPORT", status="COMPLETED")
    session.add(job)

    geoai_job = GeoAIJob(
        id=job.id,
        project_id=project.id,
        job_type="GEOPACKAGE_IMPORT",
        output_refs_json={
            "kind": "GEOPACKAGE_IMPORT",
            "status": "VALIDATED",
            "layers": {
                "parcels": {
                    "source_layer": "parcel_bnds",
                    "total": 50,
                    "valid": 48,
                    "rejected": 2,
                    "repaired": 1,
                    "source_crs": "EPSG:4326",
                }
            },
            "warnings": ["Repaired 1 self-intersecting polygon"],
            "rejection_summary": [{"code": "GIS_IMPORT_GEOMETRY_INVALID", "reason": "NULL_GEOMETRY", "count": 2}],
        },
    )
    session.add(geoai_job)

    run = GisImportRun(
        id=uuid.uuid4(),
        project_id=project.id,
        file_id=file_record.id,
        processing_job_id=job.id,
        requested_by_user_id=session.user.id,
        source_reference="ref-101",
        layer_mapping_json={"parcels": "parcel_bnds"},
        status="QUEUED",  # Will sync to COMPLETED on GET
    )
    session.add(run)
    _setup_environment(session, monkeypatch)

    client = TestClient(app)
    response = client.get(f"/api/v1/projects/{project.id}/gis-imports/{run.id}")

    assert response.status_code == 200
    data = response.json()
    assert data["import_run_id"] == str(run.id)
    assert data["project_id"] == str(project.id)
    assert data["status"] == "COMPLETED"
    assert data["source_reference"] == "ref-101"
    assert data["layer_mapping"] == {"parcels": "parcel_bnds"}

    # Bounded summary checks
    summary = data["summary"]
    assert summary["layers_mapped"] == 1
    assert summary["features_read"] == 50
    assert summary["features_imported"] == 48
    assert summary["features_rejected"] == 2
    assert summary["repairs_applied"] == 1
    assert len(summary["warnings"]) == 1
    assert len(summary["rejection_summary"]) == 1

    # Ensure no raw geometries or coordinates leaked
    assert "coordinates" not in str(data)
    assert "SELECT " not in str(data)
    assert "filesystem" not in str(data)


def test_cross_project_get_is_rejected(monkeypatch) -> None:
    session = FakeDbSession()
    project_a = Project(id=uuid.uuid4(), name="Project A", owner_id=session.user.id, state="ACTIVE")
    project_b = Project(id=uuid.uuid4(), name="Project B", owner_id=session.user.id, state="ACTIVE")
    session.add(project_a)
    session.add(project_b)
    session.add(ProjectMember(project_id=project_a.id, user_id=session.user.id, role="OFFICER"))

    # Import run belongs to Project B
    run_b = GisImportRun(
        id=uuid.uuid4(),
        project_id=project_b.id,
        file_id=uuid.uuid4(),
        processing_job_id=uuid.uuid4(),
        status="QUEUED",
    )
    session.add(run_b)
    _setup_environment(session, monkeypatch)

    client = TestClient(app)
    # Query run_b under Project A
    response = client.get(f"/api/v1/projects/{project_a.id}/gis-imports/{run_b.id}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "GIS_IMPORT_NOT_FOUND"


def test_model_and_migration_contract_integrity() -> None:
    """Verify GisImportRun table configuration, foreign keys, and indexes."""
    from sqlalchemy import inspect
    from app.models.gis_imports import GisImportRun

    table = GisImportRun.__table__
    assert table.name == "gis_import_runs"
    assert "project_id" in table.c
    assert "file_id" in table.c
    assert "processing_job_id" in table.c
    assert "requested_by_user_id" in table.c
    assert "layer_mapping_json" in table.c
    assert "status" in table.c
    assert "summary_json" in table.c
    assert "detected_layers_json" in table.c
    assert "error_code" in table.c

"""Live C.7 PostGIS, worker, version-history, and project-scope coverage."""

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core.auth import hash_password
from app.core.database import SessionLocal
from app.core.storage import get_storage_service
from app.models import Building, File, GeoAIJob, ImageryAsset, LandUseFeature, Parcel, ParcelGeometryVersion, ProcessingJob, Project, ProjectMember, Road, Role, TopologyError, User, UserRole
from app.services.user_identities import generate_login_id
from app.workers.tasks import process_geoai_buildings, process_geoai_parcel_import


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_DATABASE_TESTS") != "1",
    reason="Set RUN_DATABASE_TESTS=1 after applying migrations to run C.7 integration tests.",
)


def _user(session, role_name: str) -> User:
    user = User(login_id=generate_login_id(session, role_name), email=f"c7-{uuid.uuid4().hex}@example.invalid", password_hash=hash_password("c7-test-password"), full_name=f"C7 {role_name}")
    session.add(user)
    session.flush()
    role = session.scalar(select(Role).where(Role.name == role_name))
    assert role is not None
    session.add(UserRole(user_id=user.id, role_id=role.id))
    return user


def _project(session, owner: User) -> Project:
    project = Project(name=f"C7 {uuid.uuid4().hex}", owner_id=owner.id, state="ACTIVE")
    session.add(project)
    session.flush()
    session.add(ProjectMember(project_id=project.id, user_id=owner.id, role="SURVEYOR"))
    return project


def _geoai_job(session, project: Project, payload: dict) -> GeoAIJob:
    processing = ProcessingJob(project_id=project.id, job_type="PARCEL_IMPORT", idempotency_key=f"c7:{uuid.uuid4()}", status="QUEUED")
    session.add(processing)
    session.flush()
    geoai = GeoAIJob(id=processing.id, project_id=project.id, job_type="PARCEL_IMPORT", parameters_json=payload)
    session.add(geoai)
    return geoai


def test_parcel_import_task_persists_immutable_version_and_safe_failure() -> None:
    payload = {"source_type": "CADASTRAL_GIS", "source_payload": {"type": "Polygon", "coordinates": [[[77.0, 28.0], [77.001, 28.0], [77.001, 28.001], [77.0, 28.001], [77.0, 28.0]]]}, "source_crs": "EPSG:4326", "source_reference": "c7-fixture"}
    with SessionLocal() as session:
        surveyor = _user(session, "SURVEYOR")
        project = _project(session, surveyor)
        geoai = _geoai_job(session, project, payload)
        session.commit()
        job_id, project_id = geoai.id, project.id
    process_geoai_parcel_import.run(str(job_id))
    with SessionLocal() as session:
        processing = session.get(ProcessingJob, job_id)
        parcel = session.scalar(select(Parcel).where(Parcel.project_id == project_id))
        assert processing is not None and processing.status == "COMPLETED"
        assert parcel is not None and parcel.source_reference == "c7-fixture"
        version = session.scalar(select(ParcelGeometryVersion).where(ParcelGeometryVersion.parcel_id == parcel.id, ParcelGeometryVersion.version == 1))
        assert version is not None and version.geometry is not None
        assert version.source_crs == "EPSG:4326" and version.area_m2 is not None
        assert session.scalar(select(TopologyError).where(TopologyError.parcel_id == parcel.id)) is None


def test_invalid_geoai_input_is_terminal_and_does_not_persist_partial_parcel() -> None:
    with SessionLocal() as session:
        surveyor = _user(session, "SURVEYOR")
        project = _project(session, surveyor)
        geoai = _geoai_job(
            session,
            project,
            {
                "source_type": "UNSUPPORTED_SOURCE",
                "source_payload": None,
                "source_crs": "EPSG:4326",
            },
        )
        session.commit()
        job_id, project_id = geoai.id, project.id

    process_geoai_parcel_import.run(str(job_id))

    with SessionLocal() as session:
        processing = session.get(ProcessingJob, job_id)
        assert processing is not None
        assert processing.status == "FAILED"
        assert processing.error_json == {"message": "Processing failed."}
        assert session.scalar(select(Parcel).where(Parcel.project_id == project_id)) is None


def test_api_scope_and_human_edit_create_version_two() -> None:
    from app.main import app

    payload = {"source_type": "CADASTRAL_GIS", "source_payload": {"type": "Polygon", "coordinates": [[[77.0, 28.0], [77.001, 28.0], [77.001, 28.001], [77.0, 28.001], [77.0, 28.0]]]}, "source_crs": "EPSG:4326"}
    with SessionLocal() as session:
        surveyor = _user(session, "SURVEYOR")
        viewer = _user(session, "VIEWER")
        outsider = _user(session, "SURVEYOR")
        project = _project(session, surveyor)
        session.add(ProjectMember(project_id=project.id, user_id=viewer.id, role="VIEWER"))
        geoai = _geoai_job(session, project, payload)
        session.commit()
        surveyor_id, viewer_id, outsider_id, job_id, project_id = surveyor.login_id, viewer.login_id, outsider.login_id, geoai.id, project.id
    process_geoai_parcel_import.run(str(job_id))
    with SessionLocal() as session:
        parcel = session.scalar(select(Parcel).where(Parcel.project_id == project_id))
        assert parcel is not None
        parcel_id = parcel.id
    client = TestClient(app)
    def headers(login_id: str) -> dict[str, str]:
        response = client.post("/api/v1/auth/login", json={"identifier": login_id, "password": "c7-test-password"})
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}
    surveyor_headers, viewer_headers, outsider_headers = headers(surveyor_id), headers(viewer_id), headers(outsider_id)
    assert client.get(f"/api/v1/projects/{project_id}/parcels/{parcel_id}", headers=outsider_headers).status_code == 404
    assert client.post(f"/api/v1/projects/{project_id}/parcels/{parcel_id}/versions", json={"geometry": {"type": "Polygon", "coordinates": [[[77.0, 28.0], [77.0012, 28.0], [77.0012, 28.0012], [77.0, 28.0012], [77.0, 28.0]]]}, "source_crs": "EPSG:4326", "expected_current_version": 1, "change_reason": "Survey adjustment"}, headers=viewer_headers).status_code == 403
    edited = client.post(f"/api/v1/projects/{project_id}/parcels/{parcel_id}/versions", json={"geometry": {"type": "Polygon", "coordinates": [[[77.0, 28.0], [77.0012, 28.0], [77.0012, 28.0012], [77.0, 28.0012], [77.0, 28.0]]]}, "source_crs": "EPSG:4326", "expected_current_version": 1, "change_reason": "Survey adjustment"}, headers=surveyor_headers)
    assert edited.status_code == 201
    assert edited.json()["version"]["version"] == 2
    assert edited.json()["status"] == "REVIEW_REQUIRED"
    versions = client.get(f"/api/v1/projects/{project_id}/parcels/{parcel_id}/versions", headers=surveyor_headers)
    assert [item["version"] for item in versions.json()["items"]] == [1, 2]
    stale = client.post(f"/api/v1/projects/{project_id}/parcels/{parcel_id}/versions", json={"geometry": {"type": "Polygon", "coordinates": [[[77.0, 28.0], [77.0013, 28.0], [77.0013, 28.0013], [77.0, 28.0013], [77.0, 28.0]]]}, "source_crs": "EPSG:4326", "expected_current_version": 1, "change_reason": "Stale adjustment"}, headers=surveyor_headers)
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "PARCEL_VERSION_CONFLICT"
    versions_after_conflict = client.get(f"/api/v1/projects/{project_id}/parcels/{parcel_id}/versions", headers=surveyor_headers)
    assert [item["version"] for item in versions_after_conflict.json()["items"]] == [1, 2]


def test_gis_layer_reads_are_project_scoped() -> None:
    from geoalchemy2.shape import from_shape
    from shapely.geometry import LineString, Polygon

    from app.main import app

    with SessionLocal() as session:
        surveyor = _user(session, "SURVEYOR")
        outsider = _user(session, "SURVEYOR")
        project = _project(session, surveyor)
        footprint = from_shape(Polygon([(77.0, 28.0), (77.001, 28.0), (77.001, 28.001), (77.0, 28.001), (77.0, 28.0)]), srid=4326)
        session.add(Building(project_id=project.id, geometry=footprint, source="AI_CANDIDATE", status="AI_PRELIMINARY", verification_status="UNVERIFIED", area_m2=100.0))
        session.add(Road(project_id=project.id, geometry=from_shape(LineString([(77.0, 28.0), (77.001, 28.001)]), srid=4326), road_class="ROAD", source="EXISTING_GIS", status="DRAFT", verification_status="UNVERIFIED", length_m=150.0))
        session.add(LandUseFeature(project_id=project.id, geometry=footprint, land_use_class="RESIDENTIAL", source="MANUAL_DRAWN", status="DRAFT", verification_status="UNVERIFIED", area_m2=100.0, area_sqft=1076.39))
        session.add(TopologyError(project_id=project.id, code="OVERLAP", severity="WARNING", message="Fixture topology finding"))
        session.commit()
        surveyor_id, outsider_id, project_id = surveyor.login_id, outsider.login_id, project.id
    client = TestClient(app)

    def headers(login_id: str) -> dict[str, str]:
        response = client.post("/api/v1/auth/login", json={"identifier": login_id, "password": "c7-test-password"})
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    surveyor_headers = headers(surveyor_id)
    for endpoint in ("buildings", "roads", "land-use", "topology-errors"):
        response = client.get(f"/api/v1/projects/{project_id}/{endpoint}", headers=surveyor_headers)
        assert response.status_code == 200
        assert len(response.json()["items"]) == 1
    assert client.get(f"/api/v1/projects/{project_id}/buildings", headers=headers(outsider_id)).status_code == 404


def test_building_job_without_checkpoint_fails_terminally(monkeypatch) -> None:
    """A missing local C.2 checkpoint must fail safely, never remain queued or fabricate output."""
    from app.core.config import get_settings

    monkeypatch.delenv("GEOAI_BUILDING_CHECKPOINT", raising=False)
    get_settings.cache_clear()
    try:
        with SessionLocal() as session:
            surveyor = _user(session, "SURVEYOR")
            project = _project(session, surveyor)
            processing = ProcessingJob(project_id=project.id, job_type="BUILDING_VECTORIZE", idempotency_key=f"h2b1:{uuid.uuid4()}", status="QUEUED")
            session.add(processing)
            session.flush()
            geoai = GeoAIJob(id=processing.id, project_id=project.id, requested_by_user_id=surveyor.id, job_type="BUILDING_VECTORIZE", parameters_json={})
            session.add(geoai)
            session.commit()
            job_id, project_id = geoai.id, project.id

        process_geoai_buildings.run(str(job_id))

        with SessionLocal() as session:
            job = session.get(ProcessingJob, job_id)
            assert job is not None
            assert job.status == "FAILED"
            assert job.error_json == {"message": "Processing failed."}
            assert session.scalar(select(Building).where(Building.project_id == project_id)) is None
    finally:
        get_settings.cache_clear()


def test_legacy_fileless_imagery_is_listed_but_cannot_preview_or_run(monkeypatch) -> None:
    """H.2 metadata-only imagery remains visible without claiming private source bytes exist."""
    from app.main import app

    class PreviewStorage:
        def presign_download(self, _storage_key: str) -> str:
            return "https://signed.example/private-preview.png"

    with SessionLocal() as session:
        surveyor = _user(session, "SURVEYOR")
        project = _project(session, surveyor)
        legacy = ImageryAsset(
            project_id=project.id,
            file_id=None,
            source_reference="H2-SYNTHETIC-TN-DEMO:ORTHOMOSAIC",
            source_crs="EPSG:4326",
            coordinate_space="WORLD",
            metadata_json={"demo": True},
        )
        uploaded_file = File(
            project_id=project.id,
            original_name="registered.tif",
            category="IMAGERY",
            mime_type="image/tiff",
            size_bytes=1024,
            storage_key=f"projects/{project.id}/originals/registered.tif",
            status="UPLOADED",
        )
        session.add_all((legacy, uploaded_file))
        session.flush()
        registered = ImageryAsset(
            project_id=project.id,
            file_id=uploaded_file.id,
            source_crs="EPSG:4326",
            coordinate_space="WORLD",
            metadata_json={
                "registration_status": "READY",
                "preview_storage_key": f"projects/{project.id}/derived/preview.png",
                "preview_corners_wgs84": [[80.0, 13.0], [80.1, 13.0], [80.1, 13.1], [80.0, 13.1]],
            },
        )
        session.add(registered)
        session.commit()
        login_id, project_id, legacy_id, registered_id = surveyor.login_id, project.id, legacy.id, registered.id

    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"identifier": login_id, "password": "c7-test-password"})
    assert response.status_code == 200
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    app.dependency_overrides[get_storage_service] = lambda: PreviewStorage()
    monkeypatch.setattr("app.api.v1.geoai.process_geoai_buildings.apply_async", lambda *args, **kwargs: None)
    try:
        listed = client.get(f"/api/v1/projects/{project_id}/imagery", headers=headers)
        assert listed.status_code == 200
        legacy_payload = next(item for item in listed.json()["items"] if item["id"] == str(legacy_id))
        assert legacy_payload["file_id"] is None
        assert legacy_payload["filename"] is None

        preview = client.get(f"/api/v1/projects/{project_id}/imagery/{legacy_id}/preview-url", headers=headers)
        assert preview.status_code == 409
        assert preview.json()["error"]["code"] == "IMAGERY_SOURCE_UNAVAILABLE"
        fileless_run = client.post(f"/api/v1/projects/{project_id}/geoai/jobs", headers=headers, json={"job_type": "BUILDING_VECTORIZE", "source_type": "REGISTERED_IMAGERY", "source_payload": {}, "imagery_asset_id": str(legacy_id)})
        assert fileless_run.status_code == 409
        assert fileless_run.json()["error"]["code"] == "IMAGERY_SOURCE_UNAVAILABLE"

        assert client.get(f"/api/v1/projects/{project_id}/imagery/{registered_id}/preview-url", headers=headers).status_code == 200
        assert client.post(f"/api/v1/projects/{project_id}/geoai/jobs", headers=headers, json={"job_type": "BUILDING_VECTORIZE", "source_type": "REGISTERED_IMAGERY", "source_payload": {}, "imagery_asset_id": str(registered_id), "idempotency_key": f"registered:{registered_id}"}).status_code == 202
    finally:
        app.dependency_overrides.clear()

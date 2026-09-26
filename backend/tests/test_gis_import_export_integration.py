"""Comprehensive unit and integration tests for Step 5: GeoPackage Export Backend.

Verifies:
- Export manifest descriptors (RECORDS_CSV, PARCELS_GEOJSON, GIS_GEOPACKAGE)
- RBAC and project isolation for GET /exports/gis.gpkg
- OGC GeoPackage validity, metadata tables (gpkg_contents, gpkg_geometry_columns, gpkg_spatial_ref_sys)
- 4 GIS layers: parcels, buildings, roads, land_use
- Attribute and provenance preservation
- EPSG:4326 interchange CRS
- Empty layer handling without fabricated features
- Safe error handling with GIS_EXPORT_FAILED
- Temporary file cleanup
- Bounded audit trail
- Existing CSV and GeoJSON exports remain unchanged
"""

from __future__ import annotations

import io
import sqlite3
import tempfile
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, MultiPolygon, Polygon

from app.api.v1.platform import get_db_session
from app.core.auth import get_current_user
from app.main import app
from app.models import (
    AuditLog,
    Building,
    Document,
    LandUseFeature,
    Parcel,
    ParcelGeometryVersion,
    Project,
    ProjectMember,
    Road,
    User,
)
from app.services.geopackage import inspect_geopackage, iter_layer_features
from app.services.gis_interchange import (
    cleanup_temp_file,
    generate_project_geopackage,
)


class MockGisExportSession:
    """Mock SQLAlchemy session providing deterministic in-memory GIS project data."""

    def __init__(
        self,
        user: User | None = None,
        permissions: list[str] | None = None,
    ) -> None:
        self.user = user or User(
            id=uuid.uuid4(),
            login_id="OFF-TEST-0001",
            email="officer@example.invalid",
            password_hash="hash",
            full_name="Test Officer",
        )
        self.permissions = set(permissions if permissions is not None else ["export:read", "project:read"])
        self.projects: dict[uuid.UUID, Project] = {}
        self.members: dict[tuple[uuid.UUID, uuid.UUID], ProjectMember] = {}
        self.parcels: list[Parcel] = []
        self.parcel_versions: dict[tuple[uuid.UUID, int], ParcelGeometryVersion] = {}
        self.buildings: list[Building] = []
        self.roads: list[Road] = []
        self.land_use_features: list[LandUseFeature] = []
        self.documents: list[Document] = []
        self.audit_logs: list[AuditLog] = []
        self.committed = 0
        self.rolled_back = 0

    def add(self, item: Any) -> None:
        if isinstance(item, Project):
            self.projects[item.id] = item
        elif isinstance(item, ProjectMember):
            self.members[(item.project_id, item.user_id)] = item
        elif isinstance(item, Parcel):
            self.parcels.append(item)
        elif isinstance(item, ParcelGeometryVersion):
            self.parcel_versions[(item.parcel_id, item.version)] = item
        elif isinstance(item, Building):
            self.buildings.append(item)
        elif isinstance(item, Road):
            self.roads.append(item)
        elif isinstance(item, LandUseFeature):
            self.land_use_features.append(item)
        elif isinstance(item, Document):
            self.documents.append(item)
        elif isinstance(item, AuditLog):
            self.audit_logs.append(item)

    def add_all(self, items: list[Any]) -> None:
        for it in items:
            self.add(it)

    def get(self, model: type, identifier: Any) -> Any:
        if model is Project:
            return self.projects.get(identifier)
        return None

    def scalar(self, statement: Any) -> Any:
        sql = str(statement)
        if "FROM projects" in sql and "JOIN project_members" in sql:
            for (p_id, u_id), _ in self.members.items():
                if u_id == self.user.id and p_id in self.projects:
                    if str(p_id) in sql:
                        return self.projects[p_id]
                    return self.projects[p_id]
            return None

        if "FROM parcel_geometry_versions" in sql:
            for (p_id, ver), v_obj in self.parcel_versions.items():
                if str(p_id) in sql and f"parcel_geometry_versions.version = {ver}" in sql:
                    return v_obj
                # Fallback matching for parameters
                if str(p_id) in sql:
                    return v_obj
            # Return first matching parcel version if any
            for (p_id, ver), v_obj in self.parcel_versions.items():
                return v_obj
            return None

        if "FROM documents" in sql:
            return self.documents[0] if self.documents else None

        return None

    def scalars(self, statement: Any) -> Any:
        sql = str(statement)
        if "permissions" in sql or "roles" in sql:
            return iter(self.permissions)
        if "FROM parcels" in sql:
            target_project_id = None
            for p_id in self.projects:
                if str(p_id) in sql:
                    target_project_id = p_id
                    break
            if target_project_id:
                return iter([p for p in self.parcels if p.project_id == target_project_id])
            return iter(self.parcels)

        if "FROM buildings" in sql:
            target_project_id = None
            for p_id in self.projects:
                if str(p_id) in sql:
                    target_project_id = p_id
                    break
            if target_project_id:
                return iter([b for b in self.buildings if b.project_id == target_project_id])
            return iter(self.buildings)

        if "FROM roads" in sql:
            target_project_id = None
            for p_id in self.projects:
                if str(p_id) in sql:
                    target_project_id = p_id
                    break
            if target_project_id:
                return iter([r for r in self.roads if r.project_id == target_project_id])
            return iter(self.roads)

        if "FROM land_use_features" in sql:
            target_project_id = None
            for p_id in self.projects:
                if str(p_id) in sql:
                    target_project_id = p_id
                    break
            if target_project_id:
                return iter([l for l in self.land_use_features if l.project_id == target_project_id])
            return iter(self.land_use_features)

        if "FROM documents" in sql:
            return iter(self.documents)

        return iter([])

    def execute(self, statement: Any) -> Any:
        mock_result = MagicMock()
        mock_result.all.return_value = []
        return mock_result

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1


@pytest.fixture(autouse=True)
def reset_dependencies():
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def _seed_sample_project(session: MockGisExportSession) -> Project:
    project = Project(
        id=uuid.uuid4(),
        name="GIS Test Project",
        description="Integration export fixture",
        owner_id=session.user.id,
        state="ACTIVE",
    )
    session.add(project)
    session.add(ProjectMember(project_id=project.id, user_id=session.user.id, role="OFFICER"))

    from geoalchemy2.shape import from_shape

    # 1. Parcel with geometry version
    parcel_id = uuid.uuid4()
    parcel = Parcel(
        id=parcel_id,
        project_id=project.id,
        external_identifier="PARCEL-4326-001",
        source="DRONE_AI",
        source_reference="flight_mission_2026_09",
        status="DRAFT",
        verification_status="UNVERIFIED",
        current_geometry_version=1,
        coordinate_space="WORLD",
        source_crs="EPSG:4326",
        confidence=0.94,
        model_version="v2.1.0",
        requires_survey=True,
    )
    poly = Polygon([(80.20, 13.00), (80.21, 13.00), (80.21, 13.01), (80.20, 13.01), (80.20, 13.00)])
    version = ParcelGeometryVersion(
        id=uuid.uuid4(),
        parcel_id=parcel_id,
        version=1,
        geometry=from_shape(poly, srid=4326),
        source="DRONE_AI",
        source_reference="flight_mission_2026_09",
        coordinate_space="WORLD",
        source_crs="EPSG:4326",
        area_m2=1200.5,
        area_sqft=12922.07,
        created_by_type="AI",
    )
    session.add(parcel)
    session.add(version)

    # 2. Building
    building = Building(
        id=uuid.uuid4(),
        project_id=project.id,
        geometry=from_shape(
            Polygon([(80.202, 13.002), (80.206, 13.002), (80.206, 13.006), (80.202, 13.006), (80.202, 13.002)]),
            srid=4326,
        ),
        source="GEOAI_SEGMENTATION",
        source_reference="ortho_tile_42",
        status="DETECTED",
        verification_status="UNVERIFIED",
        area_m2=160.0,
        area_sqft=1722.23,
        model_version="bldg-det-v1",
        confidence=0.91,
        processed_at=datetime.now(UTC),
    )
    session.add(building)

    # 3. Road
    road = Road(
        id=uuid.uuid4(),
        project_id=project.id,
        geometry=from_shape(LineString([(80.199, 13.000), (80.215, 13.000)]), srid=4326),
        road_class="RESIDENTIAL_PRIMARY",
        source="SURVEY_IMPORT",
        source_reference="pwd_roads_2026",
        status="ACTIVE",
        verification_status="VERIFIED",
        length_m=1750.2,
        model_version=None,
        confidence=1.0,
        processed_at=datetime.now(UTC),
    )
    session.add(road)

    # 4. LandUseFeature
    lu = LandUseFeature(
        id=uuid.uuid4(),
        project_id=project.id,
        geometry=from_shape(
            Polygon([(80.200, 13.000), (80.220, 13.000), (80.220, 13.020), (80.200, 13.020), (80.200, 13.000)]),
            srid=4326,
        ),
        land_use_class="RESIDENTIAL",
        source="MUNICIPAL_CLASSIFICATION",
        source_reference="zone_plan_2026",
        status="PROPOSED",
        verification_status="VERIFIED",
        area_m2=48000.0,
        area_sqft=516668.0,
        model_version=None,
        confidence=0.99,
        processed_at=datetime.now(UTC),
    )
    session.add(lu)

    return project


# ==============================================================================
# 1. Export Manifest Tests (Requirements 1, 2, 3, 4)
# ==============================================================================

def test_export_manifest_contains_records_csv_parcels_geojson_and_gis_geopackage() -> None:
    """Requirements 1, 2, 3, 4: Manifest must preserve existing descriptors and include GIS_GEOPACKAGE."""
    session = MockGisExportSession()
    project = _seed_sample_project(session)

    app.dependency_overrides[get_current_user] = lambda: session.user
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app)

    response = client.get(f"/api/v1/projects/{project.id}/exports")
    assert response.status_code == 200
    data = response.json()
    assert data["project_id"] == str(project.id)
    items = {item["code"]: item for item in data["items"]}

    # Requirement 1: RECORDS_CSV unchanged
    assert "RECORDS_CSV" in items
    assert items["RECORDS_CSV"]["label"] == "Land-record evidence CSV"
    assert items["RECORDS_CSV"]["path"] == f"/api/v1/projects/{project.id}/exports/records.csv"
    assert items["RECORDS_CSV"]["media_type"] == "text/csv"

    # Requirement 2: PARCELS_GEOJSON unchanged
    assert "PARCELS_GEOJSON" in items
    assert items["PARCELS_GEOJSON"]["label"] == "Parcel GeoJSON"
    assert items["PARCELS_GEOJSON"]["path"] == f"/api/v1/projects/{project.id}/exports/parcels.geojson"
    assert items["PARCELS_GEOJSON"]["media_type"] == "application/geo+json"

    # Requirements 3 & 4: GIS_GEOPACKAGE descriptor
    assert "GIS_GEOPACKAGE" in items
    gpkg_desc = items["GIS_GEOPACKAGE"]
    assert gpkg_desc["code"] == "GIS_GEOPACKAGE"
    assert gpkg_desc["label"] == "Project GIS GeoPackage"
    assert gpkg_desc["path"] == f"/api/v1/projects/{project.id}/exports/gis.gpkg"
    assert gpkg_desc["media_type"] == "application/geopackage+sqlite3"
    assert gpkg_desc["description"] == "Project GIS evidence with provenance and verification status."


# ==============================================================================
# 2. Permissions, RBAC, and Project Isolation (Requirements 5, 6, 7)
# ==============================================================================

def test_export_geopackage_authorized_project_member() -> None:
    """Requirement 5: Authenticated project member with export:read can request GeoPackage."""
    session = MockGisExportSession(permissions=["export:read", "project:read"])
    project = _seed_sample_project(session)

    app.dependency_overrides[get_current_user] = lambda: session.user
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app)

    response = client.get(f"/api/v1/projects/{project.id}/exports/gis.gpkg")
    assert response.status_code == 200


def test_export_geopackage_rejected_without_export_read_permission() -> None:
    """Requirement 6: User without export:read is rejected with 403 Forbidden."""
    session = MockGisExportSession(permissions=["project:read"])  # lacks export:read
    project = _seed_sample_project(session)

    app.dependency_overrides[get_current_user] = lambda: session.user
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app)

    response = client.get(f"/api/v1/projects/{project.id}/exports/gis.gpkg")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PROJECT_FORBIDDEN"


def test_export_geopackage_cross_project_isolation() -> None:
    """Requirement 7: Access to a project where caller is not a member returns 404 (no leak)."""
    session = MockGisExportSession(permissions=["export:read", "project:read"])
    # Do not add caller to this other project
    other_project_id = uuid.uuid4()

    app.dependency_overrides[get_current_user] = lambda: session.user
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app)

    response = client.get(f"/api/v1/projects/{other_project_id}/exports/gis.gpkg")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "PROJECT_NOT_FOUND"


# ==============================================================================
# 3. GeoPackage Format, Metadata, and Layers (Requirements 8, 9, 10, 11, 12, 17)
# ==============================================================================

def test_export_geopackage_headers_and_validity() -> None:
    """Requirements 8, 9, 10, 11, 12, 17: Valid GeoPackage binary, headers, and metadata tables."""
    session = MockGisExportSession(permissions=["export:read", "project:read"])
    project = _seed_sample_project(session)

    app.dependency_overrides[get_current_user] = lambda: session.user
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app)

    response = client.get(f"/api/v1/projects/{project.id}/exports/gis.gpkg")
    assert response.status_code == 200

    # Requirement 8: Media type
    assert response.headers["content-type"] == "application/geopackage+sqlite3"

    # Requirement 9: File extension and disposition
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert f'filename="project-{project.id}.gpkg"' in disposition

    # Requirement 10: Valid SQLite / GeoPackage file
    content = response.content
    assert len(content) > 0

    with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)

    try:
        conn = sqlite3.connect(tmp_path)
        cur = conn.cursor()

        # Requirement 11: gpkg_contents exists
        tables = {row[0] for row in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "gpkg_contents" in tables
        assert "gpkg_geometry_columns" in tables
        assert "gpkg_spatial_ref_sys" in tables

        # Requirement 17: EPSG:4326 registered in gpkg_spatial_ref_sys
        srs_row = cur.execute("SELECT srs_id, organization, organization_coordsys_id FROM gpkg_spatial_ref_sys WHERE srs_id = 4326").fetchone()
        assert srs_row is not None
        assert srs_row[0] == 4326
        assert srs_row[1] == "EPSG"
        assert srs_row[2] == 4326

        # Requirement 12: All 4 layers registered in gpkg_contents
        content_layers = {row[0] for row in cur.execute("SELECT table_name FROM gpkg_contents")}
        assert content_layers == {"parcels", "buildings", "roads", "land_use"}

        geom_layers = {row[0]: (row[1], row[2], row[3]) for row in cur.execute("SELECT table_name, column_name, geometry_type_name, srs_id FROM gpkg_geometry_columns")}
        assert "parcels" in geom_layers
        assert geom_layers["parcels"] == ("geom", "MULTIPOLYGON", 4326)
        assert "buildings" in geom_layers
        assert geom_layers["buildings"] == ("geom", "MULTIPOLYGON", 4326)
        assert "roads" in geom_layers
        assert geom_layers["roads"] == ("geom", "MULTILINESTRING", 4326)
        assert "land_use" in geom_layers
        assert geom_layers["land_use"] == ("geom", "MULTIPOLYGON", 4326)

        conn.close()

        # Check with codebase inspection utility
        inspection = inspect_geopackage(tmp_path)
        assert inspection.layer_count == 4
        layer_map = {l.name: l for l in inspection.layers}
        assert layer_map["parcels"].crs.srs_id == 4326
        assert layer_map["parcels"].geometry_type == "MULTIPOLYGON"
        assert layer_map["buildings"].crs.srs_id == 4326
        assert layer_map["buildings"].geometry_type == "MULTIPOLYGON"
        assert layer_map["roads"].crs.srs_id == 4326
        assert layer_map["roads"].geometry_type == "MULTILINESTRING"
        assert layer_map["land_use"].crs.srs_id == 4326
        assert layer_map["land_use"].geometry_type == "MULTIPOLYGON"

    finally:
        tmp_path.unlink(missing_ok=True)


# ==============================================================================
# 4. Attribute and Provenance Preservation (Requirements 13, 14, 15, 16, 18)
# ==============================================================================

def test_attributes_and_provenance_preserved() -> None:
    """Requirements 13, 14, 15, 16, 18: Layer attributes, provenance, and geometries are preserved."""
    session = MockGisExportSession()
    project = _seed_sample_project(session)

    # Save original geometry reference to test requirement 18
    orig_parcel_geom = session.parcel_versions[(session.parcels[0].id, 1)].geometry

    temp_path, counts = generate_project_geopackage(session, project.id)
    try:
        assert counts == {"parcels": 1, "buildings": 1, "roads": 1, "land_use": 1}

        # Requirement 13: Parcel attributes
        parcel_features = list(iter_layer_features(temp_path, "parcels"))
        assert len(parcel_features) == 1
        _, geom, attrs = parcel_features[0]
        assert isinstance(geom, MultiPolygon)
        assert attrs["external_identifier"] == "PARCEL-4326-001"
        assert attrs["status"] == "DRAFT"
        assert attrs["verification_status"] == "UNVERIFIED"
        assert attrs["source"] == "DRONE_AI"
        assert attrs["source_reference"] == "flight_mission_2026_09"
        assert attrs["current_geometry_version"] == 1
        assert attrs["area_m2"] == pytest.approx(1200.5)
        assert attrs["area_sqft"] == pytest.approx(12922.07)
        assert attrs["requires_survey"] == 1
        assert attrs["model_version"] == "v2.1.0"
        assert attrs["confidence"] == pytest.approx(0.94)

        # Requirement 14: Building attributes
        bldg_features = list(iter_layer_features(temp_path, "buildings"))
        assert len(bldg_features) == 1
        _, b_geom, b_attrs = bldg_features[0]
        assert isinstance(b_geom, MultiPolygon)
        assert b_attrs["status"] == "DETECTED"
        assert b_attrs["verification_status"] == "UNVERIFIED"
        assert b_attrs["source"] == "GEOAI_SEGMENTATION"
        assert b_attrs["source_reference"] == "ortho_tile_42"
        assert b_attrs["area_m2"] == pytest.approx(160.0)
        assert b_attrs["area_sqft"] == pytest.approx(1722.23)
        assert b_attrs["model_version"] == "bldg-det-v1"
        assert b_attrs["confidence"] == pytest.approx(0.91)
        assert b_attrs["processed_at"] is not None

        # Requirement 15: Road attributes
        road_features = list(iter_layer_features(temp_path, "roads"))
        assert len(road_features) == 1
        _, r_geom, r_attrs = road_features[0]
        assert isinstance(r_geom, LineString) or str(type(r_geom)).endswith("MultiLineString'>")
        assert r_attrs["status"] == "ACTIVE"
        assert r_attrs["verification_status"] == "VERIFIED"
        assert r_attrs["source"] == "SURVEY_IMPORT"
        assert r_attrs["source_reference"] == "pwd_roads_2026"
        assert r_attrs["length_m"] == pytest.approx(1750.2)
        assert r_attrs["confidence"] == pytest.approx(1.0)
        assert r_attrs["processed_at"] is not None

        # Requirement 16: Land-use attributes
        lu_features = list(iter_layer_features(temp_path, "land_use"))
        assert len(lu_features) == 1
        _, l_geom, l_attrs = lu_features[0]
        assert isinstance(l_geom, MultiPolygon)
        assert l_attrs["status"] == "PROPOSED"
        assert l_attrs["verification_status"] == "VERIFIED"
        assert l_attrs["source"] == "MUNICIPAL_CLASSIFICATION"
        assert l_attrs["source_reference"] == "zone_plan_2026"
        assert l_attrs["land_use_class"] == "RESIDENTIAL"
        assert l_attrs["area_m2"] == pytest.approx(48000.0)

        # Requirement 18: Persisted geometry is not modified by export
        assert session.parcel_versions[(session.parcels[0].id, 1)].geometry is orig_parcel_geom

    finally:
        cleanup_temp_file(temp_path)


# ==============================================================================
# 5. Empty Project Handling (Requirement 19)
# ==============================================================================

def test_empty_project_creates_valid_schema_without_fabricated_features() -> None:
    """Requirement 19: Empty project creates all 4 valid layers with 0 features."""
    session = MockGisExportSession()
    empty_project = Project(
        id=uuid.uuid4(),
        name="Empty Project",
        owner_id=session.user.id,
        state="ACTIVE",
    )
    session.add(empty_project)
    session.add(ProjectMember(project_id=empty_project.id, user_id=session.user.id, role="OFFICER"))

    temp_path, counts = generate_project_geopackage(session, empty_project.id)
    try:
        assert counts == {"parcels": 0, "buildings": 0, "roads": 0, "land_use": 0}

        inspection = inspect_geopackage(temp_path)
        assert inspection.layer_count == 4
        for layer in inspection.layers:
            assert layer.feature_count == 0
            features = list(iter_layer_features(temp_path, layer.name))
            assert len(features) == 0

    finally:
        cleanup_temp_file(temp_path)


# ==============================================================================
# 6. Temporary File Cleanup and Audit Logging (Requirements 20, 21, 22)
# ==============================================================================

def test_temporary_export_file_is_cleaned_up() -> None:
    """Requirement 20: cleanup_temp_file removes the file and parent temporary directory."""
    temp_dir = tempfile.mkdtemp(prefix="gpkg_export_test_")
    test_file = Path(temp_dir) / "test.gpkg"
    test_file.write_bytes(b"dummy")

    assert test_file.is_file()
    cleanup_temp_file(test_file)
    assert not test_file.exists()
    assert not Path(temp_dir).exists()


def test_export_audit_event_recorded() -> None:
    """Requirement 21: Audit trail records export requested and completed events with bounded metadata."""
    session = MockGisExportSession(permissions=["export:read", "project:read"])
    project = _seed_sample_project(session)

    app.dependency_overrides[get_current_user] = lambda: session.user
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app)

    response = client.get(f"/api/v1/projects/{project.id}/exports/gis.gpkg")
    assert response.status_code == 200

    audit_actions = [log.action for log in session.audit_logs]
    assert "project.export_geopackage_requested" in audit_actions
    assert "project.export_geopackage" in audit_actions

    completed_log = next(log for log in session.audit_logs if log.action == "project.export_geopackage")
    assert completed_log.project_id == project.id
    assert "layer_counts" in completed_log.metadata_json
    assert completed_log.metadata_json["layer_counts"] == {"parcels": 1, "buildings": 1, "roads": 1, "land_use": 1}
    assert completed_log.metadata_json["total_features"] == 4
    # Ensure no large or secret data leaked in metadata
    assert "geometry" not in completed_log.metadata_json
    assert "path" not in completed_log.metadata_json


def test_export_failure_returns_safe_gis_export_failed_code(monkeypatch) -> None:
    """Requirement 22: Export failure returns safe GIS_EXPORT_FAILED and logs failure audit."""
    session = MockGisExportSession(permissions=["export:read", "project:read"])
    project = _seed_sample_project(session)

    def _broken_export(*args, **kwargs):
        raise RuntimeError("Internal database connection error")

    monkeypatch.setattr("app.api.v1.platform.generate_project_geopackage", _broken_export)

    app.dependency_overrides[get_current_user] = lambda: session.user
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app)

    response = client.get(f"/api/v1/projects/{project.id}/exports/gis.gpkg")
    assert response.status_code == 500
    data = response.json()
    assert data["error"]["code"] == "GIS_EXPORT_FAILED"
    # Ensure no internal error trace leaked
    assert "Internal database connection error" not in data["error"]["message"]

    failure_log = next(log for log in session.audit_logs if log.action == "project.export_geopackage_failed")
    assert failure_log is not None
    assert failure_log.metadata_json["error_code"] == "GIS_EXPORT_FAILED"


def test_export_failure_on_invalid_crs_transformation() -> None:
    """Safety Hardening 1: Invalid/unparseable CRS causes explicit export failure (GIS_EXPORT_FAILED)."""
    session = MockGisExportSession(permissions=["export:read", "project:read"])
    project = _seed_sample_project(session)

    # Set unparseable source CRS on the parcel version
    parcel_ver = session.parcel_versions[(session.parcels[0].id, 1)]
    parcel_ver.source_crs = "INVALID_CRS_NOT_REAL:99999"

    # Direct service call raises GeoPackageServiceError
    from app.schemas.geopackage import GIS_EXPORT_FAILED
    from app.services.geopackage import GeoPackageServiceError

    with pytest.raises(GeoPackageServiceError) as exc_info:
        generate_project_geopackage(session, project.id)
    assert exc_info.value.code == GIS_EXPORT_FAILED
    assert "Failed to parse source CRS" in str(exc_info.value)

    # API call maps to 500 GIS_EXPORT_FAILED safely without crashing
    app.dependency_overrides[get_current_user] = lambda: session.user
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app)

    response = client.get(f"/api/v1/projects/{project.id}/exports/gis.gpkg")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "GIS_EXPORT_FAILED"


def test_export_failure_on_unexpected_parcel_geometry_processing_failure() -> None:
    """Safety Hardening 2: Unexpected geometry processing error does NOT silently produce an export."""
    session = MockGisExportSession(permissions=["export:read", "project:read"])
    project = _seed_sample_project(session)

    # Assign an unsupported/corrupted object as geometry
    parcel_ver = session.parcel_versions[(session.parcels[0].id, 1)]
    parcel_ver.geometry = "CORRUPT_NOT_A_GEOMETRY_OR_WKB"

    from app.schemas.geopackage import GIS_EXPORT_FAILED
    from app.services.geopackage import GeoPackageServiceError

    with pytest.raises(GeoPackageServiceError) as exc_info:
        generate_project_geopackage(session, project.id)
    assert exc_info.value.code == GIS_EXPORT_FAILED
    assert "Failed to decode geometry for parcel" in str(exc_info.value)

    # API call returns safe GIS_EXPORT_FAILED
    app.dependency_overrides[get_current_user] = lambda: session.user
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app)

    response = client.get(f"/api/v1/projects/{project.id}/exports/gis.gpkg")
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "GIS_EXPORT_FAILED"


# ==============================================================================
# 7. Existing CSV/GeoJSON Export Regression (Requirement 23)
# ==============================================================================

def test_existing_csv_and_geojson_exports_remain_functional() -> None:
    """Requirement 23: Existing CSV and GeoJSON export endpoints still pass."""
    session = MockGisExportSession(permissions=["export:read", "project:read", "field:read", "record:read"])
    project = _seed_sample_project(session)

    app.dependency_overrides[get_current_user] = lambda: session.user
    app.dependency_overrides[get_db_session] = lambda: session
    client = TestClient(app)

    # 1. RECORDS_CSV
    csv_resp = client.get(f"/api/v1/projects/{project.id}/exports/records.csv")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "document_id,filename,workflow_status" in csv_resp.text

    # 2. PARCELS_GEOJSON
    geojson_resp = client.get(f"/api/v1/projects/{project.id}/exports/parcels.geojson")
    assert geojson_resp.status_code == 200
    assert "application/geo+json" in geojson_resp.headers["content-type"]
    geojson_data = geojson_resp.json()
    assert geojson_data["type"] == "FeatureCollection"
    assert len(geojson_data["features"]) == 1
    feat = geojson_data["features"][0]
    assert feat["properties"]["external_identifier"] == "PARCEL-4326-001"
    assert feat["properties"]["preliminary"] is True
    assert feat["properties"]["legal_boundary_asserted"] is False

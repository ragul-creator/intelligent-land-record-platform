"""Step 3 tests: GeoPackage background worker, ProcessingJob integration, and validation pipeline."""

import sqlite3
import tempfile
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from shapely.geometry import LineString, Polygon

from app.models import File, GeoAIJob, ProcessingJob
from app.schemas.geopackage import (
    GIS_IMPORT_CRS_MISSING,
    GIS_IMPORT_FILE_INVALID,
    GIS_IMPORT_LAYER_NOT_FOUND,
    GIS_IMPORT_MAPPING_INVALID,
)
from app.services.geopackage import (
    GeoPackageServiceError,
    pack_gpkg_geometry,
    process_geopackage_import_job,
)
from app.workers.tasks import process_geopackage_import


class InMemorySession:
    """Lightweight test session providing model lookups, tracking, and transaction boundaries."""

    def __init__(self) -> None:
        self.records: dict[type, dict[uuid.UUID, object]] = {
            ProcessingJob: {},
            GeoAIJob: {},
            File: {},
        }
        self.added: list[object] = []
        self.committed = 0
        self.rolled_back = 0

    def add(self, item: object) -> None:
        self.added.append(item)
        item_type = type(item)
        if item_type in self.records and hasattr(item, "id"):
            self.records[item_type][getattr(item, "id")] = item

    def get(self, model: type, identifier: uuid.UUID) -> object | None:
        return self.records.get(model, {}).get(identifier)

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None:
        self.rolled_back += 1

    def __enter__(self) -> "InMemorySession":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def _create_test_gpkg_bytes(layers_config: dict[str, dict[str, object]]) -> bytes:
    """Generate in-memory GeoPackage bytes using a temporary sqlite3 database."""
    with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as tf:
        temp_path = Path(tf.name)

    conn = sqlite3.connect(temp_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE gpkg_spatial_ref_sys (
            srs_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL PRIMARY KEY,
            organization TEXT NOT NULL,
            organization_coordsys_id INTEGER NOT NULL,
            definition TEXT NOT NULL,
            description TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE gpkg_contents (
            table_name TEXT NOT NULL PRIMARY KEY,
            data_type TEXT NOT NULL,
            identifier TEXT,
            description TEXT,
            last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
            min_x DOUBLE,
            min_y DOUBLE,
            max_x DOUBLE,
            max_y DOUBLE,
            srs_id INTEGER
        )
    """)
    cur.execute("""
        CREATE TABLE gpkg_geometry_columns (
            table_name TEXT NOT NULL,
            column_name TEXT NOT NULL,
            geometry_type_name TEXT NOT NULL,
            srs_id INTEGER NOT NULL,
            z TINYINT NOT NULL,
            m TINYINT NOT NULL,
            CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name)
        )
    """)

    # Populate CRS
    cur.execute("""
        INSERT INTO gpkg_spatial_ref_sys VALUES
        ('WGS 84', 4326, 'EPSG', 4326, 'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]', 'WGS 84')
    """)

    for table_name, cfg in layers_config.items():
        geom_type = cfg.get("geom_type", "POLYGON")
        srs_id = cfg.get("srs_id", 4326)
        columns = cfg.get("columns", {})
        features = cfg.get("features", [])

        col_defs = ", ".join(f'"{col}" {ctype}' for col, ctype in columns.items())
        create_sql = f'CREATE TABLE "{table_name}" (id INTEGER PRIMARY KEY, geom BLOB'
        if col_defs:
            create_sql += f", {col_defs}"
        create_sql += ")"
        cur.execute(create_sql)

        cur.execute(
            "INSERT INTO gpkg_contents (table_name, data_type, srs_id) VALUES (?, 'features', ?)",
            (table_name, srs_id),
        )
        cur.execute(
            "INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?, ?, 0, 0)",
            (table_name, geom_type, srs_id),
        )

        col_names = list(columns.keys())
        placeholders = ", ".join("?" for _ in col_names)
        insert_sql = f'INSERT INTO "{table_name}" (geom'
        if col_names:
            insert_sql += f', {", ".join(f"{c}" for c in col_names)}'
        insert_sql += f') VALUES (?{", " + placeholders if placeholders else ""})'

        for geom, attrs in features:
            geom_blob = pack_gpkg_geometry(geom, srs_id=int(srs_id)) if geom is not None else None
            vals = [geom_blob] + [attrs.get(c) for c in col_names]
            cur.execute(insert_sql, vals)

    conn.commit()
    conn.close()

    data = temp_path.read_bytes()
    temp_path.unlink(missing_ok=True)
    return data


@pytest.fixture
def mock_storage():
    storage = MagicMock()
    with patch("app.services.geopackage.get_storage_service", return_value=storage):
        yield storage


def test_worker_valid_parcel_geopackage_success(mock_storage) -> None:
    poly = Polygon([(80.0, 13.0), (80.1, 13.0), (80.1, 13.1), (80.0, 13.1), (80.0, 13.0)])
    gpkg_bytes = _create_test_gpkg_bytes({
        "parcels_table": {
            "geom_type": "POLYGON",
            "srs_id": 4326,
            "columns": {"khasra_no": "TEXT"},
            "features": [(poly, {"khasra_no": "101/A"})],
        }
    })
    mock_storage.read_private_object.return_value = gpkg_bytes

    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    file_id = uuid.uuid4()

    session = InMemorySession()
    job = ProcessingJob(
        id=job_id,
        project_id=project_id,
        job_type="GEOPACKAGE_IMPORT",
        status="QUEUED",
        idempotency_key="import:1",
    )
    geoai_job = GeoAIJob(
        id=job_id,
        project_id=project_id,
        job_type="GEOPACKAGE_IMPORT",
        parameters_json={
            "file_id": str(file_id),
            "layer_mapping": {"parcels": "parcels_table"},
            "import_mode": "DRAFT_IMPORT",
            "source_reference": "survey_batch_01",
        },
    )
    file_record = File(
        id=file_id,
        project_id=project_id,
        original_name="survey.gpkg",
        category="GIS_IMPORT",
        mime_type="application/geopackage+sqlite3",
        size_bytes=len(gpkg_bytes),
        storage_key=f"projects/{project_id}/originals/{file_id}/survey.gpkg",
        status="UPLOADED",
    )
    session.add(job)
    session.add(geoai_job)
    session.add(file_record)

    result = process_geopackage_import_job(session, job_id)

    assert job.status == "COMPLETED"
    assert job.progress == 100
    assert result["status"] == "VALIDATED"
    assert result["kind"] == "GEOPACKAGE_IMPORT"
    assert "parcels" in result["layers"]
    layer_res = result["layers"]["parcels"]
    assert layer_res["total"] == 1
    assert layer_res["valid"] == 1
    assert layer_res["rejected"] == 0
    assert layer_res["repaired"] == 0


def test_worker_multiple_mapped_layers(mock_storage) -> None:
    poly = Polygon([(80.0, 13.0), (80.1, 13.0), (80.1, 13.1), (80.0, 13.1), (80.0, 13.0)])
    line = LineString([(80.0, 13.0), (80.2, 13.2)])
    gpkg_bytes = _create_test_gpkg_bytes({
        "parcels_src": {"geom_type": "POLYGON", "srs_id": 4326, "features": [(poly, {})]},
        "roads_src": {"geom_type": "LINESTRING", "srs_id": 4326, "features": [(line, {})]},
    })
    mock_storage.read_private_object.return_value = gpkg_bytes

    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    file_id = uuid.uuid4()

    session = InMemorySession()
    session.add(ProcessingJob(id=job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", status="QUEUED", idempotency_key="import:multi"))
    session.add(GeoAIJob(id=job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", parameters_json={
        "file_id": str(file_id),
        "layer_mapping": {"parcels": "parcels_src", "roads": "roads_src"},
    }))
    session.add(File(id=file_id, project_id=project_id, original_name="layers.gpkg", category="GIS_IMPORT", mime_type="application/geopackage+sqlite3", size_bytes=len(gpkg_bytes), storage_key="key", status="UPLOADED"))

    result = process_geopackage_import_job(session, job_id)

    assert result["layers"]["parcels"]["valid"] == 1
    assert result["layers"]["roads"]["valid"] == 1


def test_worker_fails_on_missing_crs(mock_storage) -> None:
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    gpkg_bytes = _create_test_gpkg_bytes({
        "unreferenced": {"geom_type": "POLYGON", "srs_id": 0, "features": [(poly, {})]},
    })
    mock_storage.read_private_object.return_value = gpkg_bytes

    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    file_id = uuid.uuid4()

    session = InMemorySession()
    job = ProcessingJob(id=job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", status="QUEUED", idempotency_key="import:nocrs")
    session.add(job)
    session.add(GeoAIJob(id=job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", parameters_json={
        "file_id": str(file_id),
        "layer_mapping": {"parcels": "unreferenced"},
    }))
    session.add(File(id=file_id, project_id=project_id, original_name="nocrs.gpkg", category="GIS_IMPORT", mime_type="application/geopackage+sqlite3", size_bytes=len(gpkg_bytes), storage_key="key", status="UPLOADED"))

    with pytest.raises(GeoPackageServiceError) as exc_info:
        process_geopackage_import_job(session, job_id)

    assert exc_info.value.code == GIS_IMPORT_CRS_MISSING
    assert job.status == "FAILED"
    assert job.error_json["code"] == GIS_IMPORT_CRS_MISSING


def test_worker_fails_on_incompatible_geometry_family(mock_storage) -> None:
    line = LineString([(0, 0), (1, 1)])
    gpkg_bytes = _create_test_gpkg_bytes({
        "lines": {"geom_type": "LINESTRING", "srs_id": 4326, "features": [(line, {})]},
    })
    mock_storage.read_private_object.return_value = gpkg_bytes

    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    file_id = uuid.uuid4()

    session = InMemorySession()
    job = ProcessingJob(id=job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", status="QUEUED", idempotency_key="import:badgeom")
    session.add(job)
    session.add(GeoAIJob(id=job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", parameters_json={
        "file_id": str(file_id),
        "layer_mapping": {"parcels": "lines"},
    }))
    session.add(File(id=file_id, project_id=project_id, original_name="lines.gpkg", category="GIS_IMPORT", mime_type="application/geopackage+sqlite3", size_bytes=len(gpkg_bytes), storage_key="key", status="UPLOADED"))

    with pytest.raises(GeoPackageServiceError) as exc_info:
        process_geopackage_import_job(session, job_id)

    assert exc_info.value.code == GIS_IMPORT_MAPPING_INVALID
    assert job.status == "FAILED"


def test_worker_repaired_and_rejected_geometry_counts(mock_storage) -> None:
    bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])  # self-intersecting, repairable
    valid_poly = Polygon([(5, 5), (6, 5), (6, 6), (5, 6), (5, 5)])  # valid
    gpkg_bytes = _create_test_gpkg_bytes({
        "mixed_parcels": {
            "geom_type": "POLYGON",
            "srs_id": 4326,
            "features": [
                (valid_poly, {}),
                (bowtie, {}),
                (None, {}),  # null geometry -> rejected
            ],
        }
    })
    mock_storage.read_private_object.return_value = gpkg_bytes

    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    file_id = uuid.uuid4()

    session = InMemorySession()
    session.add(ProcessingJob(id=job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", status="QUEUED", idempotency_key="import:repair"))
    session.add(GeoAIJob(id=job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", parameters_json={
        "file_id": str(file_id),
        "layer_mapping": {"parcels": "mixed_parcels"},
    }))
    session.add(File(id=file_id, project_id=project_id, original_name="mixed.gpkg", category="GIS_IMPORT", mime_type="application/geopackage+sqlite3", size_bytes=len(gpkg_bytes), storage_key="key", status="UPLOADED"))

    result = process_geopackage_import_job(session, job_id)

    layer_stats = result["layers"]["parcels"]
    assert layer_stats["total"] == 3
    assert layer_stats["valid"] == 2
    assert layer_stats["repaired"] == 1
    assert layer_stats["rejected"] == 1

    # Check bounded rejection summary
    assert len(result["rejection_summary"]) == 1
    assert result["rejection_summary"][0]["reason"] == "NULL_GEOMETRY"
    assert result["rejection_summary"][0]["count"] == 1


def test_worker_fails_on_missing_layer_in_mapping(mock_storage) -> None:
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    gpkg_bytes = _create_test_gpkg_bytes({
        "actual_table": {"geom_type": "POLYGON", "srs_id": 4326, "features": [(poly, {})]},
    })
    mock_storage.read_private_object.return_value = gpkg_bytes

    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    file_id = uuid.uuid4()

    session = InMemorySession()
    job = ProcessingJob(id=job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", status="QUEUED", idempotency_key="import:misslayer")
    session.add(job)
    session.add(GeoAIJob(id=job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", parameters_json={
        "file_id": str(file_id),
        "layer_mapping": {"parcels": "non_existent_table"},
    }))
    session.add(File(id=file_id, project_id=project_id, original_name="test.gpkg", category="GIS_IMPORT", mime_type="application/geopackage+sqlite3", size_bytes=len(gpkg_bytes), storage_key="key", status="UPLOADED"))

    with pytest.raises(GeoPackageServiceError) as exc_info:
        process_geopackage_import_job(session, job_id)

    assert exc_info.value.code == GIS_IMPORT_LAYER_NOT_FOUND
    assert job.status == "FAILED"


def test_worker_enforces_project_isolation(mock_storage) -> None:
    gpkg_bytes = _create_test_gpkg_bytes({"layer": {"geom_type": "POLYGON", "features": []}})
    mock_storage.read_private_object.return_value = gpkg_bytes

    project_a = uuid.uuid4()
    project_b = uuid.uuid4()
    job_id = uuid.uuid4()
    file_id = uuid.uuid4()

    session = InMemorySession()
    job = ProcessingJob(id=job_id, project_id=project_a, job_type="GEOPACKAGE_IMPORT", status="QUEUED", idempotency_key="import:isolation")
    session.add(job)
    session.add(GeoAIJob(id=job_id, project_id=project_a, job_type="GEOPACKAGE_IMPORT", parameters_json={
        "file_id": str(file_id),
        "layer_mapping": {"parcels": "layer"},
    }))
    # File belongs to project_b!
    session.add(File(id=file_id, project_id=project_b, original_name="test.gpkg", category="GIS_IMPORT", mime_type="application/geopackage+sqlite3", size_bytes=len(gpkg_bytes), storage_key="key", status="UPLOADED"))

    with pytest.raises(GeoPackageServiceError) as exc_info:
        process_geopackage_import_job(session, job_id)

    assert exc_info.value.code == GIS_IMPORT_FILE_INVALID
    assert "not belong" in str(exc_info.value)
    assert job.status == "FAILED"


def test_worker_temporary_file_cleanup_on_success_and_failure(mock_storage) -> None:
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    gpkg_bytes = _create_test_gpkg_bytes({"parcels": {"geom_type": "POLYGON", "srs_id": 4326, "features": [(poly, {})]}})
    mock_storage.read_private_object.return_value = gpkg_bytes

    created_temp_paths: list[Path] = []
    original_named_temp = tempfile.NamedTemporaryFile

    def tracking_named_temp(*args, **kwargs):
        tf = original_named_temp(*args, **kwargs)
        created_temp_paths.append(Path(tf.name))
        return tf

    project_id = uuid.uuid4()
    job_id = uuid.uuid4()
    file_id = uuid.uuid4()

    session = InMemorySession()
    session.add(ProcessingJob(id=job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", status="QUEUED", idempotency_key="import:clean"))
    session.add(GeoAIJob(id=job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", parameters_json={
        "file_id": str(file_id),
        "layer_mapping": {"parcels": "parcels"},
    }))
    session.add(File(id=file_id, project_id=project_id, original_name="clean.gpkg", category="GIS_IMPORT", mime_type="application/geopackage+sqlite3", size_bytes=len(gpkg_bytes), storage_key="key", status="UPLOADED"))

    with patch("app.services.geopackage.tempfile.NamedTemporaryFile", side_effect=tracking_named_temp):
        process_geopackage_import_job(session, job_id)

    assert len(created_temp_paths) == 1
    # Verify temporary file was deleted
    assert not created_temp_paths[0].exists()

    # Now verify cleanup on failure:
    created_temp_paths.clear()
    fail_job_id = uuid.uuid4()
    fail_file_id = uuid.uuid4()
    # Bad mapping causes failure inside try block
    session.add(ProcessingJob(id=fail_job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", status="QUEUED", idempotency_key="import:fail_clean"))
    session.add(GeoAIJob(id=fail_job_id, project_id=project_id, job_type="GEOPACKAGE_IMPORT", parameters_json={
        "file_id": str(fail_file_id),
        "layer_mapping": {"parcels": "missing_layer"},
    }))
    session.add(File(id=fail_file_id, project_id=project_id, original_name="clean.gpkg", category="GIS_IMPORT", mime_type="application/geopackage+sqlite3", size_bytes=len(gpkg_bytes), storage_key="key", status="UPLOADED"))

    with patch("app.services.geopackage.tempfile.NamedTemporaryFile", side_effect=tracking_named_temp):
        with pytest.raises(GeoPackageServiceError):
            process_geopackage_import_job(session, fail_job_id)

    assert len(created_temp_paths) == 1
    assert not created_temp_paths[0].exists()


def test_worker_idempotency_ignores_non_queued_jobs(mock_storage) -> None:
    project_id = uuid.uuid4()
    job_id = uuid.uuid4()

    session = InMemorySession()
    # Already COMPLETED job
    completed_job = ProcessingJob(
        id=job_id,
        project_id=project_id,
        job_type="GEOPACKAGE_IMPORT",
        status="COMPLETED",
        idempotency_key="import:completed",
    )
    session.add(completed_job)

    result = process_geopackage_import_job(session, job_id)
    assert result == {}
    assert completed_job.status == "COMPLETED"
    assert not mock_storage.read_private_object.called


def test_celery_task_execution_handles_domain_error_safely() -> None:
    job_uuid = uuid.uuid4()
    mock_session = InMemorySession()
    job = ProcessingJob(
        id=job_uuid,
        project_id=uuid.uuid4(),
        job_type="GEOPACKAGE_IMPORT",
        status="QUEUED",
        idempotency_key="task:1",
    )
    mock_session.add(job)

    with patch("app.workers.tasks.SessionLocal", return_value=mock_session), \
         patch("app.services.geopackage.process_geopackage_import_job", side_effect=GeoPackageServiceError("Layer missing", code=GIS_IMPORT_LAYER_NOT_FOUND)):
        # Run Celery task directly
        process_geopackage_import.apply(args=[str(job_uuid)])

    assert job.status == "FAILED"
    assert job.error_json["code"] == GIS_IMPORT_LAYER_NOT_FOUND

"""Step 1 focused tests: GeoPackage data contracts and file policy hardening."""

import importlib.util
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.models import foundation
from app.services.file_policy import (
    ALLOWED_CONTENT_TYPES,
    FileCategory,
    UploadMetadata,
    upload_permission_for_category,
)


def test_gis_import_file_category_is_defined() -> None:
    assert FileCategory.GIS_IMPORT == "GIS_IMPORT"
    assert FileCategory.GIS_IMPORT.value == "GIS_IMPORT"


def test_existing_categories_still_work() -> None:
    expected_categories = {"DOCUMENT", "IMAGERY", "GIS", "SUPPORTING", "GIS_IMPORT"}
    actual_categories = {category.value for category in FileCategory}
    assert actual_categories == expected_categories


@pytest.mark.parametrize(
    "mime_type",
    [
        "application/geopackage+sqlite3",
        "application/x-sqlite3",
        "application/octet-stream",
    ],
)
def test_valid_gpkg_and_supported_mime_types_accepted(mime_type: str) -> None:
    metadata = UploadMetadata(
        filename="boundary_survey.gpkg",
        content_type=mime_type,
        size_bytes=1024 * 1024,
        category=FileCategory.GIS_IMPORT,
    )
    assert metadata.category == FileCategory.GIS_IMPORT
    assert metadata.filename == "boundary_survey.gpkg"
    assert metadata.content_type == mime_type


@pytest.mark.parametrize(
    "invalid_filename",
    [
        "survey.geojson",
        "survey.zip",
        "survey.sqlite",
        "survey.shp",
        "survey.tif",
        "survey.pdf",
        "survey_gpkg",
        "survey.gpkg.zip",
    ],
)
def test_invalid_extensions_rejected_for_gis_import(invalid_filename: str) -> None:
    with pytest.raises(ValueError, match=r"\.gpkg extension"):
        UploadMetadata(
            filename=invalid_filename,
            content_type="application/geopackage+sqlite3",
            size_bytes=1024,
            category=FileCategory.GIS_IMPORT,
        )


@pytest.mark.parametrize(
    "unsupported_mime",
    [
        "application/geo+json",
        "application/json",
        "application/zip",
        "image/tiff",
        "application/pdf",
        "text/plain",
    ],
)
def test_unsupported_mime_types_rejected_for_gis_import(unsupported_mime: str) -> None:
    with pytest.raises(ValueError, match="Content type is not allowed"):
        UploadMetadata(
            filename="survey.gpkg",
            content_type=unsupported_mime,
            size_bytes=1024,
            category=FileCategory.GIS_IMPORT,
        )


def test_gis_import_requires_geo_edit_draft_permission() -> None:
    assert upload_permission_for_category(FileCategory.GIS_IMPORT) == "geo:edit_draft"
    assert upload_permission_for_category("GIS_IMPORT") == "geo:edit_draft"


def test_existing_permissions_unchanged() -> None:
    assert upload_permission_for_category(FileCategory.IMAGERY) == "imagery:upload"
    assert upload_permission_for_category("IMAGERY") == "imagery:upload"
    assert upload_permission_for_category(FileCategory.DOCUMENT) == "document:upload"
    assert upload_permission_for_category("DOCUMENT") == "document:upload"
    assert upload_permission_for_category(FileCategory.GIS) == "document:upload"
    assert upload_permission_for_category("GIS") == "document:upload"
    assert upload_permission_for_category(FileCategory.SUPPORTING) == "document:upload"
    assert upload_permission_for_category("SUPPORTING") == "document:upload"


def test_existing_gis_behavior_not_broken() -> None:
    assert FileCategory.GIS in ALLOWED_CONTENT_TYPES
    assert "application/geo+json" in ALLOWED_CONTENT_TYPES[FileCategory.GIS]
    assert "application/json" in ALLOWED_CONTENT_TYPES[FileCategory.GIS]
    assert "application/zip" in ALLOWED_CONTENT_TYPES[FileCategory.GIS]

    meta_geojson = UploadMetadata(
        filename="parcels.geojson",
        content_type="application/geo+json",
        size_bytes=2048,
        category=FileCategory.GIS,
    )
    assert meta_geojson.category == FileCategory.GIS

    meta_zip = UploadMetadata(
        filename="shapefile.zip",
        content_type="application/zip",
        size_bytes=4096,
        category=FileCategory.GIS,
    )
    assert meta_zip.category == FileCategory.GIS


def test_db_check_constraint_ck_files_category_includes_gis_import() -> None:
    file_constraints = {
        c.name: c for c in foundation.File.__table__.constraints if c.name == "ck_files_category"
    }
    assert "ck_files_category" in file_constraints
    sql = str(file_constraints["ck_files_category"].sqltext)
    for cat in ("DOCUMENT", "IMAGERY", "GIS", "SUPPORTING", "GIS_IMPORT"):
        assert f"'{cat}'" in sql


def test_db_check_constraint_ck_geoai_jobs_type_includes_geopackage_import() -> None:
    geoai_constraints = {
        c.name: c for c in foundation.GeoAIJob.__table__.constraints if c.name == "ck_geoai_jobs_type"
    }
    assert "ck_geoai_jobs_type" in geoai_constraints
    sql = str(geoai_constraints["ck_geoai_jobs_type"].sqltext)
    for job_type in (
        "PARCEL_IMPORT",
        "BUILDING_VECTORIZE",
        "ROAD_VECTORIZE",
        "ROAD_IMPORT",
        "LAND_USE_IMPORT",
        "TOPOLOGY_VALIDATE",
        "GEOPACKAGE_IMPORT",
    ):
        assert f"'{job_type}'" in sql


def test_alembic_migration_metadata_and_operations() -> None:
    migration_path = (
        Path(__file__).resolve().parent.parent
        / "alembic"
        / "versions"
        / "20260926_11_geopackage_data_contracts.py"
    )
    assert migration_path.exists(), f"Migration not found at {migration_path}"

    spec = importlib.util.spec_from_file_location("migration_20260926_11", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration.revision == "20260926_11"
    assert migration.down_revision == "20260922_10"
    assert callable(migration.upgrade)
    assert callable(migration.downgrade)

    # Test upgrade call with mocked op
    mock_op = MagicMock()
    original_op = migration.op
    try:
        migration.op = mock_op
        migration.upgrade()
        # Verify drop and create constraints
        drop_calls = [call.args for call in mock_op.drop_constraint.call_args_list]
        create_calls = [call.args for call in mock_op.create_check_constraint.call_args_list]
        assert ("ck_files_category", "files") in drop_calls
        assert ("ck_geoai_jobs_type", "geoai_jobs") in drop_calls
        assert any(
            c[0] == "ck_files_category" and "'GIS_IMPORT'" in c[2] for c in create_calls
        )
        assert any(
            c[0] == "ck_geoai_jobs_type" and "'GEOPACKAGE_IMPORT'" in c[2] for c in create_calls
        )

        # Test downgrade call with mocked op
        mock_op.reset_mock()
        migration.downgrade()
        downgrade_drop_calls = [call.args for call in mock_op.drop_constraint.call_args_list]
        downgrade_create_calls = [call.args for call in mock_op.create_check_constraint.call_args_list]
        assert ("ck_geoai_jobs_type", "geoai_jobs") in downgrade_drop_calls
        assert ("ck_files_category", "files") in downgrade_drop_calls
        assert any(
            c[0] == "ck_files_category" and "'GIS_IMPORT'" not in c[2] for c in downgrade_create_calls
        )
        assert any(
            c[0] == "ck_geoai_jobs_type" and "'GEOPACKAGE_IMPORT'" not in c[2] for c in downgrade_create_calls
        )
    finally:
        migration.op = original_op

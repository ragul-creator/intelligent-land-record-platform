"""Focused unit and service tests for Step 2: GeoPackage schemas, inspection, CRS, and geometry validation."""

import sqlite3
from pathlib import Path

import pytest
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Point, Polygon

from app.schemas.geopackage import (
    EXPECTED_GEOMETRY_FAMILIES,
    EXPECTED_LAYER_ATTRIBUTES,
    GIS_IMPORT_CRS_MISSING,
    GIS_IMPORT_FILE_INVALID,
    GIS_IMPORT_GEOMETRY_INVALID,
    GIS_IMPORT_LAYER_NOT_FOUND,
    GIS_IMPORT_MAPPING_INVALID,
    CRSInfo,
    LayerInspection,
    LayerMappingRequest,
    RejectionSummary,
)
from app.services.geopackage import (
    GeoPackageServiceError,
    bounded_rejection_summary,
    detect_crs,
    inspect_geopackage,
    iter_layer_features,
    pack_gpkg_geometry,
    transform_to_interchange_crs,
    unpack_gpkg_geometry,
    validate_geometry,
    validate_layer_mapping,
)


def _build_test_gpkg(
    path: Path,
    layers_config: dict[str, dict[str, object]],
    custom_crs: list[tuple[str, int, str, int, str]] | None = None,
) -> Path:
    """Helper to construct a valid, standards-compliant OGC GeoPackage test database."""
    conn = sqlite3.connect(path)
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

    # Populate CRS table
    srs_records = custom_crs or [
        ("WGS 84", 4326, "EPSG", 4326, 'GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'),
        ("WGS 84 / Pseudo-Mercator", 3857, "EPSG", 3857, 'PROJCS["WGS 84 / Pseudo-Mercator",GEOGCS["WGS 84",DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563]],PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Mercator_1SP"],PARAMETER["central_meridian",0],PARAMETER["scale_factor",1],PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["metre",1]]'),
    ]
    for rec in srs_records:
        cur.execute(
            "INSERT INTO gpkg_spatial_ref_sys (srs_name, srs_id, organization, organization_coordsys_id, definition) VALUES (?, ?, ?, ?, ?)",
            rec,
        )

    # Populate layers
    for table_name, cfg in layers_config.items():
        geom_type = cfg.get("geom_type", "POLYGON")
        srs_id = cfg.get("srs_id", 4326)
        columns = cfg.get("columns", {})  # name: sqlite type
        features = cfg.get("features", [])  # list of (shapely_geom, {attr_dict})

        col_defs = ", ".join(f'"{c_name}" {c_type}' for c_name, c_type in columns.items())
        create_sql = f'CREATE TABLE "{table_name}" (id INTEGER PRIMARY KEY, geom BLOB'
        if col_defs:
            create_sql += f", {col_defs}"
        create_sql += ")"
        cur.execute(create_sql)

        # Register in contents and geometry_columns
        cur.execute(
            "INSERT INTO gpkg_contents (table_name, data_type, srs_id) VALUES (?, 'features', ?)",
            (table_name, srs_id),
        )
        cur.execute(
            "INSERT INTO gpkg_geometry_columns VALUES (?, 'geom', ?, ?, 0, 0)",
            (table_name, geom_type, srs_id),
        )

        # Insert features
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
    return path


def test_schema_layer_mapping_request_validation() -> None:
    # Valid mapping
    req = LayerMappingRequest(
        parcels="cadastral_parcels",
        buildings="building_footprints",
        roads="centerlines",
        land_use="zoning_areas",
    )
    assert req.parcels == "cadastral_parcels"
    assert req.buildings == "building_footprints"
    assert req.import_mode == "DRAFT_IMPORT"

    # Empty string rejected
    with pytest.raises(ValueError, match="non-empty string"):
        LayerMappingRequest(parcels="   ")

    # At least one layer required
    with pytest.raises(ValueError, match="At least one logical layer must be mapped"):
        LayerMappingRequest()

    # Duplicate source layer assigned to multiple targets is rejected
    with pytest.raises(ValueError, match="Duplicate source layer assignment detected"):
        LayerMappingRequest(parcels="survey_layer", buildings="survey_layer")


def test_schema_layer_attributes_contract_definitions() -> None:
    for layer in ("parcels", "buildings", "roads", "land_use"):
        assert layer in EXPECTED_LAYER_ATTRIBUTES
        attrs = EXPECTED_LAYER_ATTRIBUTES[layer]
        assert "id" in attrs
        assert "status" in attrs
        assert "source" in attrs
        assert "verification_status" in attrs


def test_unpack_and_pack_gpkg_geometry_roundtrip() -> None:
    poly = Polygon([(80.0, 13.0), (80.1, 13.0), (80.1, 13.1), (80.0, 13.1), (80.0, 13.0)])
    blob = pack_gpkg_geometry(poly, srs_id=4326)
    assert blob is not None
    assert blob[:2] == b"GP"

    srs_id, unpacked = unpack_gpkg_geometry(blob)
    assert srs_id == 4326
    assert unpacked is not None
    assert unpacked.equals(poly)

    # Empty geometry
    empty_poly = Polygon()
    empty_blob = pack_gpkg_geometry(empty_poly, srs_id=4326)
    assert empty_blob is not None
    srs_id_empty, unpacked_empty = unpack_gpkg_geometry(empty_blob)
    assert srs_id_empty == 4326
    assert unpacked_empty is None


def test_inspect_geopackage_discovers_multiple_layers(tmp_path: Path) -> None:
    gpkg_path = tmp_path / "multi_layer.gpkg"
    p1 = Polygon([(80.1, 13.0), (80.2, 13.0), (80.2, 13.1), (80.1, 13.1), (80.1, 13.0)])
    l1 = LineString([(80.1, 13.0), (80.2, 13.1)])

    _build_test_gpkg(
        gpkg_path,
        {
            "cadastral_parcels": {
                "geom_type": "POLYGON",
                "srs_id": 4326,
                "columns": {"parcel_id": "TEXT", "owner_name": "TEXT"},
                "features": [(p1, {"parcel_id": "P-101", "owner_name": "Govt"})],
            },
            "road_lines": {
                "geom_type": "LINESTRING",
                "srs_id": 4326,
                "columns": {"road_name": "TEXT", "lanes": "INTEGER"},
                "features": [(l1, {"road_name": "Main Road", "lanes": 2})],
            },
        },
    )

    inspection = inspect_geopackage(gpkg_path)
    assert inspection.layer_count == 2
    layer_names = {l.name for l in inspection.layers}
    assert layer_names == {"cadastral_parcels", "road_lines"}

    parcel_layer = next(l for l in inspection.layers if l.name == "cadastral_parcels")
    assert parcel_layer.geometry_type.upper() == "POLYGON"
    assert parcel_layer.feature_count == 1
    assert "parcel_id" in parcel_layer.fields
    assert "owner_name" in parcel_layer.fields
    assert parcel_layer.crs.detected is True
    assert parcel_layer.crs.crs_string == "EPSG:4326"

    road_layer = next(l for l in inspection.layers if l.name == "road_lines")
    assert road_layer.geometry_type.upper() == "LINESTRING"
    assert road_layer.feature_count == 1
    assert "road_name" in road_layer.fields


def test_inspect_geopackage_rejects_non_geopackage_file(tmp_path: Path) -> None:
    non_gpkg = tmp_path / "fake.gpkg"
    non_gpkg.write_text("not a sqlite file", encoding="utf-8")

    with pytest.raises(GeoPackageServiceError) as exc_info:
        inspect_geopackage(non_gpkg)
    assert exc_info.value.code == GIS_IMPORT_FILE_INVALID

    missing_path = tmp_path / "does_not_exist.gpkg"
    with pytest.raises(GeoPackageServiceError) as exc_info:
        inspect_geopackage(missing_path)
    assert exc_info.value.code == GIS_IMPORT_FILE_INVALID


def test_detect_crs_reports_missing_crs(tmp_path: Path) -> None:
    gpkg_path = tmp_path / "missing_crs.gpkg"
    p1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])

    _build_test_gpkg(
        gpkg_path,
        {
            "unreferenced": {
                "geom_type": "POLYGON",
                "srs_id": 0,  # Undefined SRS
                "columns": {"name": "TEXT"},
                "features": [(p1, {"name": "item"})],
            }
        },
        custom_crs=[("Undefined", 0, "NONE", 0, "")],
    )

    inspection = inspect_geopackage(gpkg_path)
    layer = inspection.layers[0]
    assert layer.crs.detected is False

    with pytest.raises(GeoPackageServiceError) as exc_info:
        detect_crs(layer)
    assert exc_info.value.code == GIS_IMPORT_CRS_MISSING


def test_validate_layer_mapping_checks_existence_and_geometry_compatibility(tmp_path: Path) -> None:
    gpkg_path = tmp_path / "layers.gpkg"
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    line = LineString([(0, 0), (1, 1)])

    _build_test_gpkg(
        gpkg_path,
        {
            "poly_layer": {"geom_type": "POLYGON", "srs_id": 4326, "features": [(poly, {})]},
            "line_layer": {"geom_type": "LINESTRING", "srs_id": 4326, "features": [(line, {})]},
        },
    )

    inspection = inspect_geopackage(gpkg_path)

    # Valid mapping
    valid_map = LayerMappingRequest(parcels="poly_layer", roads="line_layer")
    resolved = validate_layer_mapping(inspection, valid_map)
    assert "parcels" in resolved
    assert "roads" in resolved
    assert resolved["parcels"].name == "poly_layer"
    assert resolved["roads"].name == "line_layer"

    # Missing layer
    missing_map = LayerMappingRequest(parcels="non_existent_table")
    with pytest.raises(GeoPackageServiceError) as exc_info:
        validate_layer_mapping(inspection, missing_map)
    assert exc_info.value.code == GIS_IMPORT_LAYER_NOT_FOUND

    # Incompatible geometry: mapping line_layer to parcels (expects Polygon/MultiPolygon)
    incompatible_map = LayerMappingRequest(parcels="line_layer")
    with pytest.raises(GeoPackageServiceError) as exc_info:
        validate_layer_mapping(inspection, incompatible_map)
    assert exc_info.value.code == GIS_IMPORT_MAPPING_INVALID


def test_transform_to_interchange_crs() -> None:
    # Point at (0, 0) in EPSG:3857 should transform to (0, 0) in EPSG:4326
    pt_3857 = Point(0.0, 0.0)
    transformed = transform_to_interchange_crs(pt_3857, "EPSG:3857")
    assert abs(transformed.x) < 1e-6
    assert abs(transformed.y) < 1e-6

    # Identity transform for 4326
    pt_4326 = Point(80.2, 13.0)
    same = transform_to_interchange_crs(pt_4326, "EPSG:4326")
    assert same.x == 80.2
    assert same.y == 13.0

    # Missing CRS raises error
    missing_crs = CRSInfo(detected=False)
    with pytest.raises(GeoPackageServiceError) as exc_info:
        transform_to_interchange_crs(pt_4326, missing_crs)
    assert exc_info.value.code == GIS_IMPORT_CRS_MISSING


def test_validate_geometry_polygonal_family() -> None:
    # Valid Polygon
    valid_poly = Polygon([(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)])
    geom, repaired, err, warn = validate_geometry(valid_poly, ("Polygon", "MultiPolygon"))
    assert geom is not None
    assert repaired is False
    assert err is None

    # Null geometry
    geom, repaired, err, warn = validate_geometry(None, ("Polygon", "MultiPolygon"))
    assert geom is None
    assert err == GIS_IMPORT_GEOMETRY_INVALID
    assert warn == "NULL_GEOMETRY"

    # Empty geometry
    geom, repaired, err, warn = validate_geometry(Polygon(), ("Polygon", "MultiPolygon"))
    assert geom is None
    assert err == GIS_IMPORT_GEOMETRY_INVALID
    assert warn == "EMPTY_GEOMETRY"

    # Incompatible geometry type (LineString for polygonal layer)
    geom, repaired, err, warn = validate_geometry(LineString([(0, 0), (1, 1)]), ("Polygon", "MultiPolygon"))
    assert geom is None
    assert err == GIS_IMPORT_GEOMETRY_INVALID
    assert warn == "UNEXPECTED_NON_POLYGONAL_TYPE"

    # Self-intersecting bow-tie polygon (conservatively repaired via make_valid)
    bowtie = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])
    assert not bowtie.is_valid
    repaired_geom, was_repaired, err, warn = validate_geometry(bowtie, ("Polygon", "MultiPolygon"))
    assert was_repaired is True
    assert repaired_geom is not None
    assert repaired_geom.is_valid
    assert isinstance(repaired_geom, (Polygon, MultiPolygon))
    assert err is None


def test_validate_geometry_linear_family() -> None:
    # Valid LineString
    valid_line = LineString([(0, 0), (5, 5), (10, 5)])
    geom, repaired, err, warn = validate_geometry(valid_line, ("LineString", "MultiLineString"))
    assert geom is not None
    assert repaired is False
    assert err is None

    # Polygon rejected for linear layer
    poly = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    geom, repaired, err, warn = validate_geometry(poly, ("LineString", "MultiLineString"))
    assert geom is None
    assert err == GIS_IMPORT_GEOMETRY_INVALID
    assert warn == "UNEXPECTED_NON_LINEAR_TYPE"


def test_bounded_rejection_summary_does_not_leak_source_records() -> None:
    rejections = [
        (GIS_IMPORT_GEOMETRY_INVALID, "NULL_GEOMETRY"),
        (GIS_IMPORT_GEOMETRY_INVALID, "NULL_GEOMETRY"),
        (GIS_IMPORT_GEOMETRY_INVALID, "EMPTY_GEOMETRY"),
    ]
    summary = bounded_rejection_summary(rejections)
    assert len(summary) == 2
    null_summary = next(s for s in summary if s.reason == "NULL_GEOMETRY")
    assert null_summary.count == 2
    assert null_summary.error_code == GIS_IMPORT_GEOMETRY_INVALID

    empty_summary = next(s for s in summary if s.reason == "EMPTY_GEOMETRY")
    assert empty_summary.count == 1

    # Verify no source rows or payload exist in RejectionSummary
    for s in summary:
        assert set(s.model_dump().keys()) == {"error_code", "reason", "count"}


def test_iter_layer_features_streaming(tmp_path: Path) -> None:
    gpkg_path = tmp_path / "stream.gpkg"
    p1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)])
    p2 = Polygon([(2, 2), (3, 2), (3, 3), (2, 3), (2, 2)])

    _build_test_gpkg(
        gpkg_path,
        {
            "stream_parcels": {
                "geom_type": "POLYGON",
                "srs_id": 4326,
                "columns": {"khasra_no": "TEXT", "area": "REAL"},
                "features": [
                    (p1, {"khasra_no": "100/1", "area": 120.5}),
                    (p2, {"khasra_no": "100/2", "area": 250.0}),
                ],
            }
        },
    )

    rows = list(iter_layer_features(gpkg_path, "stream_parcels"))
    assert len(rows) == 2

    idx0, geom0, attrs0 = rows[0]
    assert idx0 == 0
    assert geom0 is not None and geom0.equals(p1)
    assert attrs0["khasra_no"] == "100/1"
    assert attrs0["area"] == 120.5

    idx1, geom1, attrs1 = rows[1]
    assert idx1 == 1
    assert geom1 is not None and geom1.equals(p2)
    assert attrs1["khasra_no"] == "100/2"

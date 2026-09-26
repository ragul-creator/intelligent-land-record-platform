"""GeoPackage GIS interchange export service.

Generates standards-compliant OGC GeoPackage files (EPSG:4326) from persisted
project GIS evidence (parcels, buildings, roads, land_use).
"""

from __future__ import annotations

import logging
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any

from pyproj import CRS, Transformer
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Building, LandUseFeature, Parcel, Road
from app.schemas.geopackage import GIS_EXPORT_FAILED
from app.services.geoai import current_version
from app.services.geopackage import GeoPackageServiceError, pack_gpkg_geometry

logger = logging.getLogger(__name__)

# Standard WGS84 WKT definition for OGC GeoPackage
WGS84_WKT = (
    'GEOGCS["WGS 84",'
    'DATUM["WGS_1984",SPHEROID["WGS 84",6378137,298.257223563,AUTHORITY["EPSG","6326"]],AUTHORITY["EPSG","6326"]],'
    'PRIMEM["Greenwich",0,AUTHORITY["EPSG","8901"]],'
    'UNIT["degree",0.0174532925199433,AUTHORITY["EPSG","9122"]],'
    'AXIS["Latitude",NORTH],AXIS["Longitude",EAST],'
    'AUTHORITY["EPSG","4326"]]'
)

SRS_RECORDS = [
    (
        "Undefined cartesian coordinate reference systems",
        -1,
        "NONE",
        -1,
        "undefined",
        "undefined cartesian coordinate reference system",
    ),
    (
        "Undefined geographic coordinate reference systems",
        0,
        "NONE",
        0,
        "undefined",
        "undefined geographic coordinate reference system",
    ),
    (
        "WGS 84",
        4326,
        "EPSG",
        4326,
        WGS84_WKT,
        "WGS 84 geographic coordinate reference system",
    ),
]


def _to_shape_strict(geom: Any | None, entity_name: str, entity_id: Any) -> BaseGeometry | None:
    """Convert persisted geometry to Shapely BaseGeometry; raises explicit error on corruption."""
    if geom is None:
        return None
    if isinstance(geom, BaseGeometry):
        return geom
    try:
        from geoalchemy2.shape import to_shape

        return to_shape(geom)
    except Exception as err:
        raise GeoPackageServiceError(
            f"Failed to decode geometry for {entity_name} '{entity_id}': {err}",
            code=GIS_EXPORT_FAILED,
        ) from err


def _ensure_epsg4326(geom: BaseGeometry, source_crs: str | None = None) -> BaseGeometry:
    """Transform geometry to EPSG:4326 interchange CRS; fails explicitly on invalid or failed transforms."""
    if source_crs and source_crs.strip():
        crs_str = source_crs.strip()
        try:
            crs_obj = CRS.from_user_input(crs_str)
        except Exception as err:
            raise GeoPackageServiceError(
                f"Failed to parse source CRS '{crs_str}' for GeoPackage export: {err}",
                code=GIS_EXPORT_FAILED,
            ) from err

        if crs_obj.to_epsg() == 4326:
            return geom

        try:
            transformer = Transformer.from_crs(crs_obj, "EPSG:4326", always_xy=True)
            transformed = transform_geometry(transformer.transform, geom)
            if transformed is None or transformed.is_empty:
                raise GeoPackageServiceError(
                    f"Geometry became empty after transformation from '{crs_str}' to EPSG:4326.",
                    code=GIS_EXPORT_FAILED,
                )
            return transformed
        except Exception as err:
            if isinstance(err, GeoPackageServiceError):
                raise
            raise GeoPackageServiceError(
                f"Coordinate transformation to EPSG:4326 failed from '{crs_str}': {err}",
                code=GIS_EXPORT_FAILED,
            ) from err
    return geom


def _normalize_polygon(geom: BaseGeometry, entity_name: str, entity_id: Any) -> MultiPolygon:
    """Ensure polygon geometry is represented as MultiPolygon; raises explicit error if incompatible."""
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    if isinstance(geom, MultiPolygon):
        return geom
    raise GeoPackageServiceError(
        f"Unexpected non-polygonal geometry type '{type(geom).__name__}' for {entity_name} '{entity_id}'.",
        code=GIS_EXPORT_FAILED,
    )


def _normalize_line(geom: BaseGeometry, entity_name: str, entity_id: Any) -> MultiLineString:
    """Ensure linear geometry is represented as MultiLineString; raises explicit error if incompatible."""
    if isinstance(geom, LineString):
        return MultiLineString([geom])
    if isinstance(geom, MultiLineString):
        return geom
    raise GeoPackageServiceError(
        f"Unexpected non-linear geometry type '{type(geom).__name__}' for {entity_name} '{entity_id}'.",
        code=GIS_EXPORT_FAILED,
    )


def cleanup_temp_file(path: str | Path | None) -> None:
    """Best-effort cleanup of temporary file and parent export directory."""
    if path is None:
        return
    p = Path(path)
    try:
        if p.is_file():
            p.unlink(missing_ok=True)
        parent = p.parent
        if parent.name.startswith("gpkg_export_"):
            parent.rmdir()
    except Exception as err:
        logger.warning("Failed to clean up temporary export file %s: %s", p, err)


def generate_project_geopackage(session: Session, project_id: uuid.UUID) -> tuple[Path, dict[str, int]]:
    """Generate a temporary OGC GeoPackage with 4 layers from persisted PostGIS records.

    Layers:
    1. parcels (MULTIPOLYGON, EPSG:4326)
    2. buildings (MULTIPOLYGON, EPSG:4326)
    3. roads (MULTILINESTRING, EPSG:4326)
    4. land_use (MULTIPOLYGON, EPSG:4326)

    Returns:
        (path_to_temporary_gpkg, layer_counts_dict)
    """
    temp_dir = tempfile.mkdtemp(prefix="gpkg_export_")
    gpkg_path = Path(temp_dir) / f"project-{project_id}.gpkg"

    conn = None
    try:
        conn = sqlite3.connect(gpkg_path)
        cur = conn.cursor()

        # Enable foreign keys
        cur.execute("PRAGMA foreign_keys = ON")

        # 1. Create standard GeoPackage metadata tables
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
                description TEXT DEFAULT '',
                last_change DATETIME NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                min_x DOUBLE,
                min_y DOUBLE,
                max_x DOUBLE,
                max_y DOUBLE,
                srs_id INTEGER,
                CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id)
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
                CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name),
                CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
                CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id)
            )
        """)

        # Populate standard SRS records
        for srs in SRS_RECORDS:
            cur.execute(
                "INSERT INTO gpkg_spatial_ref_sys (srs_name, srs_id, organization, organization_coordsys_id, definition, description) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                srs,
            )

        # 2. Register layers in gpkg_contents first (satisfies foreign key fk_gc_tn)
        cur.execute(
            "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, srs_id) "
            "VALUES ('parcels', 'features', 'parcels', 'Project parcel boundaries and verification evidence', 4326)"
        )
        cur.execute(
            "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, srs_id) "
            "VALUES ('buildings', 'features', 'buildings', 'Project building footprints and attribution', 4326)"
        )
        cur.execute(
            "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, srs_id) "
            "VALUES ('roads', 'features', 'roads', 'Project road centerlines and attribution', 4326)"
        )
        cur.execute(
            "INSERT INTO gpkg_contents (table_name, data_type, identifier, description, srs_id) "
            "VALUES ('land_use', 'features', 'land_use', 'Project land use classification polygons', 4326)"
        )

        # Register layers in gpkg_geometry_columns
        cur.execute("INSERT INTO gpkg_geometry_columns VALUES ('parcels', 'geom', 'MULTIPOLYGON', 4326, 0, 0)")
        cur.execute("INSERT INTO gpkg_geometry_columns VALUES ('buildings', 'geom', 'MULTIPOLYGON', 4326, 0, 0)")
        cur.execute("INSERT INTO gpkg_geometry_columns VALUES ('roads', 'geom', 'MULTILINESTRING', 4326, 0, 0)")
        cur.execute("INSERT INTO gpkg_geometry_columns VALUES ('land_use', 'geom', 'MULTIPOLYGON', 4326, 0, 0)")

        # 3. Create the 4 feature tables
        cur.execute("""
            CREATE TABLE "parcels" (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                id TEXT NOT NULL,
                external_identifier TEXT,
                status TEXT NOT NULL,
                verification_status TEXT NOT NULL,
                source TEXT NOT NULL,
                source_reference TEXT,
                current_geometry_version INTEGER NOT NULL,
                area_m2 DOUBLE,
                area_sqft DOUBLE,
                requires_survey INTEGER NOT NULL,
                model_version TEXT,
                confidence DOUBLE
            )
        """)

        cur.execute("""
            CREATE TABLE "buildings" (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                id TEXT NOT NULL,
                status TEXT NOT NULL,
                verification_status TEXT NOT NULL,
                source TEXT NOT NULL,
                source_reference TEXT,
                area_m2 DOUBLE,
                area_sqft DOUBLE,
                model_version TEXT,
                confidence DOUBLE,
                processed_at TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE "roads" (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                id TEXT NOT NULL,
                status TEXT NOT NULL,
                verification_status TEXT NOT NULL,
                source TEXT NOT NULL,
                source_reference TEXT,
                road_class TEXT,
                length_m DOUBLE,
                model_version TEXT,
                confidence DOUBLE,
                processed_at TEXT
            )
        """)

        cur.execute("""
            CREATE TABLE "land_use" (
                fid INTEGER PRIMARY KEY AUTOINCREMENT,
                geom BLOB,
                id TEXT NOT NULL,
                status TEXT NOT NULL,
                verification_status TEXT NOT NULL,
                source TEXT NOT NULL,
                source_reference TEXT,
                land_use_class TEXT,
                area_m2 DOUBLE,
                area_sqft DOUBLE,
                model_version TEXT,
                confidence DOUBLE,
                processed_at TEXT
            )
        """)

        layer_counts: dict[str, int] = {"parcels": 0, "buildings": 0, "roads": 0, "land_use": 0}

        # --- A. Export Parcels ---
        parcels = list(
            session.scalars(
                select(Parcel).where(Parcel.project_id == project_id).order_by(Parcel.created_at, Parcel.id)
            )
        )
        p_bounds: list[float] | None = None
        for parcel in parcels:
            try:
                version = current_version(session, parcel)
            except Exception as err:
                raise GeoPackageServiceError(
                    f"Failed to resolve current geometry version for parcel '{parcel.id}': {err}",
                    code=GIS_EXPORT_FAILED,
                ) from err

            area_m2 = version.area_m2
            area_sqft = version.area_sqft
            geom_blob = None

            if version.geometry is not None:
                shape_geom = _to_shape_strict(version.geometry, "parcel", parcel.id)
                if shape_geom is not None and not shape_geom.is_empty:
                    transformed_geom = _ensure_epsg4326(shape_geom, version.source_crs)
                    norm_geom = _normalize_polygon(transformed_geom, "parcel", parcel.id)
                    try:
                        geom_blob = pack_gpkg_geometry(norm_geom, srs_id=4326)
                    except Exception as err:
                        raise GeoPackageServiceError(
                            f"Failed to pack GeoPackage geometry for parcel '{parcel.id}': {err}",
                            code=GIS_EXPORT_FAILED,
                        ) from err

                    b = norm_geom.bounds
                    if p_bounds is None:
                        p_bounds = [b[0], b[1], b[2], b[3]]
                    else:
                        p_bounds[0] = min(p_bounds[0], b[0])
                        p_bounds[1] = min(p_bounds[1], b[1])
                        p_bounds[2] = max(p_bounds[2], b[2])
                        p_bounds[3] = max(p_bounds[3], b[3])

            cur.execute(
                """
                INSERT INTO "parcels" (
                    geom, id, external_identifier, status, verification_status,
                    source, source_reference, current_geometry_version,
                    area_m2, area_sqft, requires_survey, model_version, confidence
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    geom_blob,
                    str(parcel.id),
                    parcel.external_identifier,
                    parcel.status,
                    parcel.verification_status,
                    parcel.source,
                    parcel.source_reference,
                    parcel.current_geometry_version,
                    area_m2,
                    area_sqft,
                    1 if parcel.requires_survey else 0,
                    parcel.model_version,
                    parcel.confidence,
                ),
            )
            layer_counts["parcels"] += 1

        if p_bounds:
            cur.execute(
                "UPDATE gpkg_contents SET min_x = ?, min_y = ?, max_x = ?, max_y = ? WHERE table_name = 'parcels'",
                (p_bounds[0], p_bounds[1], p_bounds[2], p_bounds[3]),
            )

        # --- B. Export Buildings ---
        buildings = list(
            session.scalars(
                select(Building).where(Building.project_id == project_id).order_by(Building.created_at, Building.id)
            )
        )
        b_bounds: list[float] | None = None
        for building in buildings:
            geom_blob = None
            if building.geometry is not None:
                shape_geom = _to_shape_strict(building.geometry, "building", building.id)
                if shape_geom is not None and not shape_geom.is_empty:
                    transformed_geom = _ensure_epsg4326(shape_geom)
                    norm_geom = _normalize_polygon(transformed_geom, "building", building.id)
                    try:
                        geom_blob = pack_gpkg_geometry(norm_geom, srs_id=4326)
                    except Exception as err:
                        raise GeoPackageServiceError(
                            f"Failed to pack GeoPackage geometry for building '{building.id}': {err}",
                            code=GIS_EXPORT_FAILED,
                        ) from err

                    b = norm_geom.bounds
                    if b_bounds is None:
                        b_bounds = [b[0], b[1], b[2], b[3]]
                    else:
                        b_bounds[0] = min(b_bounds[0], b[0])
                        b_bounds[1] = min(b_bounds[1], b[1])
                        b_bounds[2] = max(b_bounds[2], b[2])
                        b_bounds[3] = max(b_bounds[3], b[3])

            cur.execute(
                """
                INSERT INTO "buildings" (
                    geom, id, status, verification_status, source, source_reference,
                    area_m2, area_sqft, model_version, confidence, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    geom_blob,
                    str(building.id),
                    building.status,
                    building.verification_status,
                    building.source,
                    building.source_reference,
                    building.area_m2,
                    building.area_sqft,
                    building.model_version,
                    building.confidence,
                    building.processed_at.isoformat() if building.processed_at else None,
                ),
            )
            layer_counts["buildings"] += 1

        if b_bounds:
            cur.execute(
                "UPDATE gpkg_contents SET min_x = ?, min_y = ?, max_x = ?, max_y = ? WHERE table_name = 'buildings'",
                (b_bounds[0], b_bounds[1], b_bounds[2], b_bounds[3]),
            )

        # --- C. Export Roads ---
        roads = list(
            session.scalars(
                select(Road).where(Road.project_id == project_id).order_by(Road.created_at, Road.id)
            )
        )
        r_bounds: list[float] | None = None
        for road in roads:
            geom_blob = None
            if road.geometry is not None:
                shape_geom = _to_shape_strict(road.geometry, "road", road.id)
                if shape_geom is not None and not shape_geom.is_empty:
                    transformed_geom = _ensure_epsg4326(shape_geom)
                    norm_geom = _normalize_line(transformed_geom, "road", road.id)
                    try:
                        geom_blob = pack_gpkg_geometry(norm_geom, srs_id=4326)
                    except Exception as err:
                        raise GeoPackageServiceError(
                            f"Failed to pack GeoPackage geometry for road '{road.id}': {err}",
                            code=GIS_EXPORT_FAILED,
                        ) from err

                    b = norm_geom.bounds
                    if r_bounds is None:
                        r_bounds = [b[0], b[1], b[2], b[3]]
                    else:
                        r_bounds[0] = min(r_bounds[0], b[0])
                        r_bounds[1] = min(r_bounds[1], b[1])
                        r_bounds[2] = max(r_bounds[2], b[2])
                        r_bounds[3] = max(r_bounds[3], b[3])

            cur.execute(
                """
                INSERT INTO "roads" (
                    geom, id, status, verification_status, source, source_reference,
                    road_class, length_m, model_version, confidence, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    geom_blob,
                    str(road.id),
                    road.status,
                    road.verification_status,
                    road.source,
                    road.source_reference,
                    road.road_class,
                    road.length_m,
                    road.model_version,
                    road.confidence,
                    road.processed_at.isoformat() if road.processed_at else None,
                ),
            )
            layer_counts["roads"] += 1

        if r_bounds:
            cur.execute(
                "UPDATE gpkg_contents SET min_x = ?, min_y = ?, max_x = ?, max_y = ? WHERE table_name = 'roads'",
                (r_bounds[0], r_bounds[1], r_bounds[2], r_bounds[3]),
            )

        # --- D. Export Land Use ---
        land_use_features = list(
            session.scalars(
                select(LandUseFeature).where(LandUseFeature.project_id == project_id).order_by(LandUseFeature.created_at, LandUseFeature.id)
            )
        )
        lu_bounds: list[float] | None = None
        for lu in land_use_features:
            geom_blob = None
            if lu.geometry is not None:
                shape_geom = _to_shape_strict(lu.geometry, "land_use", lu.id)
                if shape_geom is not None and not shape_geom.is_empty:
                    transformed_geom = _ensure_epsg4326(shape_geom)
                    norm_geom = _normalize_polygon(transformed_geom, "land_use", lu.id)
                    try:
                        geom_blob = pack_gpkg_geometry(norm_geom, srs_id=4326)
                    except Exception as err:
                        raise GeoPackageServiceError(
                            f"Failed to pack GeoPackage geometry for land_use '{lu.id}': {err}",
                            code=GIS_EXPORT_FAILED,
                        ) from err

                    b = norm_geom.bounds
                    if lu_bounds is None:
                        lu_bounds = [b[0], b[1], b[2], b[3]]
                    else:
                        lu_bounds[0] = min(lu_bounds[0], b[0])
                        lu_bounds[1] = min(lu_bounds[1], b[1])
                        lu_bounds[2] = max(lu_bounds[2], b[2])
                        lu_bounds[3] = max(lu_bounds[3], b[3])

            cur.execute(
                """
                INSERT INTO "land_use" (
                    geom, id, status, verification_status, source, source_reference,
                    land_use_class, area_m2, area_sqft, model_version, confidence, processed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    geom_blob,
                    str(lu.id),
                    lu.status,
                    lu.verification_status,
                    lu.source,
                    lu.source_reference,
                    lu.land_use_class,
                    lu.area_m2,
                    lu.area_sqft,
                    lu.model_version,
                    lu.confidence,
                    lu.processed_at.isoformat() if lu.processed_at else None,
                ),
            )
            layer_counts["land_use"] += 1

        if lu_bounds:
            cur.execute(
                "UPDATE gpkg_contents SET min_x = ?, min_y = ?, max_x = ?, max_y = ? WHERE table_name = 'land_use'",
                (lu_bounds[0], lu_bounds[1], lu_bounds[2], lu_bounds[3]),
            )

        conn.commit()
        return gpkg_path, layer_counts

    except Exception:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            conn = None
        cleanup_temp_file(gpkg_path)
        raise
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass

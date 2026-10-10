"""GeoPackage GIS interchange service core: inspection, mapping, CRS, and geometry validation."""

from __future__ import annotations

import math
import sqlite3
import struct
import tempfile
import uuid
from datetime import UTC, datetime
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from geoalchemy2.shape import from_shape
from pyproj import CRS, Geod, Transformer
from shapely import make_valid
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon, mapping
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry
from shapely.ops import unary_union
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.core.storage import get_storage_service
from app.models import (
    Building,
    File,
    GeoAIJob,
    LandUseFeature,
    Parcel,
    ParcelGeometryVersion,
    ProcessingJob,
    Road,
)
from app.models.gis_imports import GisImportRun, sync_gis_import_run
from app.services.sync import record_sync_change
from app.schemas.geopackage import (
    EXPECTED_GEOMETRY_FAMILIES,
    GIS_IMPORT_CRS_MISSING,
    GIS_IMPORT_FILE_INVALID,
    GIS_IMPORT_GEOMETRY_INVALID,
    GIS_IMPORT_LAYER_NOT_FOUND,
    GIS_IMPORT_MAPPING_INVALID,
    CRSInfo,
    GeoPackageImportJobResult,
    GeoPackageImportLayerResult,
    GeoPackageInspection,
    GeometryValidationResult,
    LayerInspection,
    LayerMappingRequest,
    LogicalLayer,
    RejectionSummary,
)
from app.services.processing_jobs import mark_job_completed, mark_job_processing


NAMESPACE_GEOPACKAGE_IMPORT = uuid.UUID("a7e58db6-10f8-47e2-8951-5079a4059d68")


class GeoPackageServiceError(ValueError):
    """Domain exception for GeoPackage processing failures."""

    def __init__(self, message: str, code: str = GIS_IMPORT_FILE_INVALID, details: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def unpack_gpkg_geometry(blob: bytes | None) -> tuple[int | None, BaseGeometry | None]:
    """Unpack a standard OGC GeoPackage binary geometry blob into (srs_id, shapely_geom).

    Specification: OGC 12-128r15 section 2.1.3.
    """
    if not blob:
        return None, None

    # Standard GeoPackage binary header starts with 'GP' (0x47, 0x50)
    if len(blob) >= 8 and blob[:2] == b"GP":
        flags = blob[3]
        endian = "<" if (flags & 0x01) else ">"
        is_empty = bool(flags & 0x10)
        envelope_indicator = (flags >> 1) & 0x07
        envelope_byte_lengths = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}
        if envelope_indicator not in envelope_byte_lengths:
            raise GeoPackageServiceError(
                "Invalid envelope indicator in GeoPackage geometry header.",
                code=GIS_IMPORT_GEOMETRY_INVALID,
            )
        env_size = envelope_byte_lengths[envelope_indicator]
        srs_id = struct.unpack(f"{endian}i", blob[4:8])[0]
        header_len = 8 + env_size

        if is_empty or len(blob) <= header_len:
            return srs_id, None

        wkb_bytes = blob[header_len:]
        try:
            import shapely

            geom = shapely.from_wkb(wkb_bytes)
            return srs_id, geom
        except Exception as err:
            raise GeoPackageServiceError(
                "Corrupt WKB payload inside GeoPackage feature.",
                code=GIS_IMPORT_GEOMETRY_INVALID,
            ) from err

    # Fallback: attempt direct WKB parsing if header is absent
    try:
        import shapely

        geom = shapely.from_wkb(blob)
        return None, geom
    except Exception as err:
        raise GeoPackageServiceError(
            "Unsupported binary geometry format in GeoPackage.",
            code=GIS_IMPORT_GEOMETRY_INVALID,
        ) from err


def pack_gpkg_geometry(geom: BaseGeometry | None, srs_id: int = 4326) -> bytes | None:
    """Pack a shapely geometry into standard OGC GeoPackage binary format."""
    if geom is None:
        return None

    import shapely

    flags = 0x01  # little-endian, no envelope, standard
    if geom.is_empty:
        flags |= 0x10
        header = struct.pack("<2sBBi", b"GP", 0, flags, srs_id)
        return header

    header = struct.pack("<2sBBi", b"GP", 0, flags, srs_id)
    wkb = shapely.to_wkb(geom)
    return header + wkb


def _parse_srs_info(cursor: sqlite3.Cursor, srs_id: int | None, tables: set[str]) -> CRSInfo:
    """Inspect and resolve CRS from gpkg_spatial_ref_sys table without guessing."""
    if srs_id is None or srs_id in (0, -1):
        return CRSInfo(detected=False, srs_id=srs_id)

    if "gpkg_spatial_ref_sys" not in tables:
        return CRSInfo(detected=False, srs_id=srs_id)

    row = cursor.execute(
        "SELECT srs_name, srs_id, organization, organization_coordsys_id, definition "
        "FROM gpkg_spatial_ref_sys WHERE srs_id = ?",
        (srs_id,),
    ).fetchone()

    if not row:
        return CRSInfo(detected=False, srs_id=srs_id)

    srs_name, stored_srs_id, org, org_code, definition = row

    if org in ("NONE", "None", "", None) or org_code in (0, -1, None):
        if not definition or definition.strip().upper() in ("UNDEFINED", "NONE", ""):
            return CRSInfo(detected=False, srs_id=stored_srs_id)

    # Attempt to resolve via pyproj
    try:
        if org and org.upper() == "EPSG" and org_code and int(org_code) > 0:
            parsed = CRS.from_epsg(int(org_code))
            return CRSInfo(
                detected=True,
                srs_id=stored_srs_id,
                authority="EPSG",
                code=int(org_code),
                crs_string=f"EPSG:{org_code}",
                is_geographic=parsed.is_geographic,
                is_projected=parsed.is_projected,
            )

        if definition and definition.strip():
            parsed = CRS.from_user_input(definition)
            epsg_code = parsed.to_epsg()
            return CRSInfo(
                detected=True,
                srs_id=stored_srs_id,
                authority=parsed.to_authority()[0] if parsed.to_authority() else org,
                code=epsg_code or org_code,
                crs_string=parsed.to_string(),
                is_geographic=parsed.is_geographic,
                is_projected=parsed.is_projected,
            )

        if org and org_code:
            parsed = CRS.from_user_input(f"{org}:{org_code}")
            return CRSInfo(
                detected=True,
                srs_id=stored_srs_id,
                authority=org,
                code=org_code,
                crs_string=parsed.to_string(),
                is_geographic=parsed.is_geographic,
                is_projected=parsed.is_projected,
            )
    except Exception:
        pass

    return CRSInfo(detected=False, srs_id=stored_srs_id)


def inspect_geopackage(path: str | Path) -> GeoPackageInspection:
    """Inspect all vector feature layers, schemas, counts, and CRS metadata of a GeoPackage file."""
    file_path = Path(path).resolve()
    if not file_path.is_file():
        raise GeoPackageServiceError(
            f"File not found or is not a regular file: {file_path}",
            code=GIS_IMPORT_FILE_INVALID,
        )

    # Read-only SQLite URI prevents file modification
    uri = f"file:{file_path.as_posix()}?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as err:
        raise GeoPackageServiceError(
            "Could not open file as SQLite/GeoPackage.",
            code=GIS_IMPORT_FILE_INVALID,
        ) from err

    try:
        try:
            cursor = conn.cursor()
            tables = {row[0] for row in cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "gpkg_contents" not in tables:
                raise GeoPackageServiceError(
                    "Missing gpkg_contents table: file is not a valid OGC GeoPackage.",
                    code=GIS_IMPORT_FILE_INVALID,
                )

            # Query feature layers from gpkg_contents and gpkg_geometry_columns
            query = """
                SELECT
                    c.table_name,
                    c.srs_id,
                    g.column_name,
                    g.geometry_type_name
                FROM gpkg_contents c
                LEFT JOIN gpkg_geometry_columns g ON c.table_name = g.table_name
                WHERE c.data_type = 'features' OR g.table_name IS NOT NULL
            """
            layer_rows = cursor.execute(query).fetchall()

            inspected_layers: list[LayerInspection] = []
            for table_name, c_srs_id, g_col, g_type in layer_rows:
                if table_name not in tables:
                    continue

                safe_table = table_name.replace('"', '""')
                geom_col = g_col or "geom"

                # Feature count
                count_res = cursor.execute(f'SELECT COUNT(*) FROM "{safe_table}"').fetchone()
                feature_count = count_res[0] if count_res else 0

                # Fields / Columns (excluding geometry column)
                table_info = cursor.execute(f'PRAGMA table_info("{safe_table}")').fetchall()
                fields = [col[1] for col in table_info if col[1] != geom_col]

                # Detect CRS
                effective_srs_id = c_srs_id if c_srs_id is not None else 0
                crs_info = _parse_srs_info(cursor, effective_srs_id, tables)

                # Determine geometry type
                resolved_geom_type = g_type or "GEOMETRY"
                if resolved_geom_type.upper() in ("GEOMETRY", "UNKNOWN", "") and feature_count > 0:
                    safe_geom_col = geom_col.replace('"', '""')
                    sample = cursor.execute(
                        f'SELECT "{safe_geom_col}" FROM "{safe_table}" WHERE "{safe_geom_col}" IS NOT NULL LIMIT 1'
                    ).fetchone()
                    if sample and sample[0]:
                        try:
                            _, s_geom = unpack_gpkg_geometry(sample[0])
                            if s_geom is not None:
                                resolved_geom_type = s_geom.geom_type
                        except Exception:
                            pass

                inspected_layers.append(
                    LayerInspection(
                        name=table_name,
                        geometry_type=resolved_geom_type,
                        feature_count=feature_count,
                        crs=crs_info,
                        fields=fields,
                    )
                )

            return GeoPackageInspection(
                file_path=str(file_path),
                layer_count=len(inspected_layers),
                layers=inspected_layers,
            )
        except sqlite3.Error as err:
            raise GeoPackageServiceError(
                f"Corrupt or invalid SQLite/GeoPackage file: {err}",
                code=GIS_IMPORT_FILE_INVALID,
            ) from err
    finally:
        conn.close()


def detect_crs(layer: LayerInspection) -> CRSInfo:
    """Verify and return the detected CRS for a layer; never guesses a missing CRS."""
    if not layer.crs.detected or not layer.crs.crs_string:
        raise GeoPackageServiceError(
            f"Layer '{layer.name}' is missing a valid CRS.",
            code=GIS_IMPORT_CRS_MISSING,
            details={"layer": layer.name, "srs_id": layer.crs.srs_id},
        )
    return layer.crs


def validate_layer_mapping(
    inspection: GeoPackageInspection,
    mapping: LayerMappingRequest,
) -> dict[LogicalLayer, LayerInspection]:
    """Verify that every mapped layer exists, matches expected geometry families, and has detected CRS."""
    layer_map = {layer.name: layer for layer in inspection.layers}
    resolved: dict[LogicalLayer, LayerInspection] = {}

    for logical_key in ("parcels", "buildings", "roads", "land_use"):
        source_name = getattr(mapping, logical_key, None)
        if not source_name:
            continue

        if source_name not in layer_map:
            raise GeoPackageServiceError(
                f"Mapped source layer '{source_name}' was not found in the GeoPackage.",
                code=GIS_IMPORT_LAYER_NOT_FOUND,
                details={"logical_layer": logical_key, "source_layer": source_name},
            )

        layer = layer_map[source_name]

        # Geometry family compatibility check
        expected_types = EXPECTED_GEOMETRY_FAMILIES.get(logical_key, ())
        normalized_layer_geom = layer.geometry_type.upper()
        type_matched = any(
            exp.upper() in normalized_layer_geom or normalized_layer_geom in exp.upper()
            for exp in expected_types
        )
        if not type_matched and normalized_layer_geom != "GEOMETRY":
            raise GeoPackageServiceError(
                f"Source layer '{source_name}' has geometry type '{layer.geometry_type}', "
                f"which is incompatible with logical layer '{logical_key}' (expected {expected_types}).",
                code=GIS_IMPORT_MAPPING_INVALID,
                details={
                    "logical_layer": logical_key,
                    "source_layer": source_name,
                    "actual_geometry": layer.geometry_type,
                    "expected_geometries": expected_types,
                },
            )

        # Enforce detectable CRS
        detect_crs(layer)

        resolved[logical_key] = layer

    return resolved


def transform_to_interchange_crs(
    geometry: BaseGeometry,
    source_crs: str | CRSInfo | CRS,
) -> BaseGeometry:
    """Transform geometry to EPSG:4326 interchange CRS; fails explicitly if CRS is unknown."""
    if isinstance(source_crs, CRSInfo):
        if not source_crs.detected or not source_crs.crs_string:
            raise GeoPackageServiceError(
                "Cannot transform geometry without a detected source CRS.",
                code=GIS_IMPORT_CRS_MISSING,
            )
        crs_input = source_crs.crs_string
    elif isinstance(source_crs, CRS):
        crs_input = source_crs
    elif isinstance(source_crs, str):
        if not source_crs.strip():
            raise GeoPackageServiceError(
                "Source CRS cannot be empty.",
                code=GIS_IMPORT_CRS_MISSING,
            )
        crs_input = source_crs.strip()
    else:
        raise GeoPackageServiceError(
            "Invalid source CRS representation.",
            code=GIS_IMPORT_CRS_MISSING,
        )

    try:
        parsed_source = CRS.from_user_input(crs_input)
    except Exception as err:
        raise GeoPackageServiceError(
            f"Invalid source CRS '{crs_input}'.",
            code=GIS_IMPORT_CRS_MISSING,
        ) from err

    if parsed_source.to_epsg() == 4326:
        return geometry

    try:
        transformer = Transformer.from_crs(parsed_source, "EPSG:4326", always_xy=True)
        return transform_geometry(transformer.transform, geometry)
    except Exception as err:
        raise GeoPackageServiceError(
            f"Coordinate transformation to EPSG:4326 failed: {err}",
            code=GIS_IMPORT_GEOMETRY_INVALID,
        ) from err


def _repair_polygonal(geom: BaseGeometry) -> Polygon | MultiPolygon | None:
    """Conservative repair of polygonal geometry without fabricating boundaries."""
    candidate = geom
    if not candidate.is_valid:
        candidate = make_valid(candidate)
    if candidate.is_empty:
        return None
    if isinstance(candidate, (Polygon, MultiPolygon)):
        return candidate
    if isinstance(candidate, GeometryCollection):
        parts = [p for p in candidate.geoms if isinstance(p, (Polygon, MultiPolygon)) and not p.is_empty]
        if parts:
            merged = unary_union(parts)
            if isinstance(merged, (Polygon, MultiPolygon)) and not merged.is_empty:
                return merged
    return None


def _repair_linear(geom: BaseGeometry) -> LineString | MultiLineString | None:
    """Conservative repair of linear geometry without inventing connections."""
    candidate = geom
    if not candidate.is_valid:
        candidate = make_valid(candidate)
    if candidate.is_empty:
        return None
    if isinstance(candidate, (LineString, MultiLineString)):
        return candidate
    if isinstance(candidate, GeometryCollection):
        parts = [p for p in candidate.geoms if isinstance(p, (LineString, MultiLineString)) and not p.is_empty]
        if parts:
            merged = unary_union(parts)
            if isinstance(merged, (LineString, MultiLineString)) and not merged.is_empty:
                return merged
    return None


def validate_geometry(
    geom: BaseGeometry | None,
    expected_family: str | tuple[str, ...],
) -> tuple[BaseGeometry | None, bool, str | None, str | None]:
    """Validate and conservatively repair input geometry.

    Returns:
        (valid_geometry, was_repaired, rejection_code, warning_or_repair_reason)
    """
    if geom is None:
        return None, False, GIS_IMPORT_GEOMETRY_INVALID, "NULL_GEOMETRY"

    if geom.is_empty:
        return None, False, GIS_IMPORT_GEOMETRY_INVALID, "EMPTY_GEOMETRY"

    # Coordinate finiteness check
    bounds = geom.bounds
    if not all(math.isfinite(b) for b in bounds):
        return None, False, GIS_IMPORT_GEOMETRY_INVALID, "COORDINATES_NON_FINITE"

    # Family normalization
    family = tuple(expected_family) if isinstance(expected_family, (tuple, list)) else (expected_family,)
    is_polygonal = any("POLYGON" in f.upper() for f in family)
    is_linear = any("LINE" in f.upper() for f in family)

    if is_polygonal and not isinstance(geom, (Polygon, MultiPolygon)):
        # Check if geometry collection contains polygons
        if isinstance(geom, GeometryCollection):
            repaired = _repair_polygonal(geom)
            if repaired is not None and repaired.is_valid and not repaired.is_empty:
                return repaired, True, None, "EXTRACTED_POLYGONS_FROM_COLLECTION"
        return None, False, GIS_IMPORT_GEOMETRY_INVALID, "UNEXPECTED_NON_POLYGONAL_TYPE"

    if is_linear and not isinstance(geom, (LineString, MultiLineString)):
        if isinstance(geom, GeometryCollection):
            repaired = _repair_linear(geom)
            if repaired is not None and repaired.is_valid and not repaired.is_empty:
                return repaired, True, None, "EXTRACTED_LINES_FROM_COLLECTION"
        return None, False, GIS_IMPORT_GEOMETRY_INVALID, "UNEXPECTED_NON_LINEAR_TYPE"

    # Validity check & conservative repair
    if not geom.is_valid:
        if is_polygonal:
            repaired = _repair_polygonal(geom)
            if repaired is not None and repaired.is_valid and not repaired.is_empty:
                return repaired, True, None, "CONSERVATIVELY_REPAIRED_POLYGON"
        elif is_linear:
            repaired = _repair_linear(geom)
            if repaired is not None and repaired.is_valid and not repaired.is_empty:
                return repaired, True, None, "CONSERVATIVELY_REPAIRED_LINE"
        return None, False, GIS_IMPORT_GEOMETRY_INVALID, "GEOMETRY_REPAIR_FAILED"

    return geom, False, None, None


def bounded_rejection_summary(rejections: list[tuple[str, str]]) -> list[RejectionSummary]:
    """Aggregate rejection diagnostics into bounded counts without leaking source records."""
    counts = Counter(rejections)
    return [
        RejectionSummary(error_code=code, reason=reason, count=cnt)
        for (code, reason), cnt in sorted(counts.items())
    ]


def _calculate_geodesic_area_and_sqft(geom: BaseGeometry) -> tuple[float, float]:
    """Calculate geodesic area in m2 and sq ft for WGS84 polygon."""
    try:
        geod = Geod(ellps="WGS84")
        area_m2, _ = geod.geometry_area_perimeter(geom)
        m2 = round(abs(float(area_m2)), 2)
        sqft = round(m2 * 10.7639104167, 2)
        return m2, sqft
    except Exception:
        return 0.0, 0.0


def _calculate_geodesic_length_m(geom: BaseGeometry) -> float:
    """Calculate geodesic length in meters for WGS84 line."""
    try:
        geod = Geod(ellps="WGS84")
        length_m = geod.geometry_length(geom)
        return round(abs(float(length_m)), 2)
    except Exception:
        return 0.0


def iter_layer_features(
    path: str | Path,
    layer_name: str,
    geom_column: str | None = None,
) -> Iterator[tuple[int, BaseGeometry | None, dict[str, Any]]]:
    """Stream features from a GeoPackage layer safely using a cursor.

    Yields:
        (row_index, shapely_geometry_or_none, attributes_dict)
    """
    file_path = Path(path).resolve()
    uri = f"file:{file_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.cursor()
        safe_table = layer_name.replace('"', '""')

        # Determine geometry column if not specified
        if not geom_column:
            geom_row = cursor.execute(
                "SELECT column_name FROM gpkg_geometry_columns WHERE table_name = ?",
                (layer_name,),
            ).fetchone()
            geom_col = geom_row[0] if geom_row else "geom"
        else:
            geom_col = geom_column

        cursor.execute(f'SELECT * FROM "{safe_table}"')
        row_idx = 0
        for row in cursor:
            row_dict = dict(row)
            geom_blob = row_dict.pop(geom_col, None)
            geom: BaseGeometry | None = None
            feat_srs_id: int | None = None
            if geom_blob is not None:
                try:
                    feat_srs_id, geom = unpack_gpkg_geometry(geom_blob)
                except Exception:
                    geom = None
            if feat_srs_id is not None:
                row_dict["_feature_srs_id"] = feat_srs_id
            yield row_idx, geom, row_dict
            row_idx += 1
    finally:
        conn.close()


def process_geopackage_import_job(session: Session, job_id: uuid.UUID) -> dict[str, Any]:
    """Execute GeoPackage validation pipeline for a GEOPACKAGE_IMPORT processing job.

    Performs:
    1. ProcessingJob & GeoAIJob resolution and status validation.
    2. File resolution, category check, and project isolation check.
    3. Storage fetch to temporary local file with guaranteed cleanup.
    4. Inspection and layer mapping validation.
    5. Per-layer feature streaming, geometry validation, repair, and transformation to EPSG:4326.
    6. Bounded metrics accumulation and ProcessingJob completion.
    """
    job = session.get(ProcessingJob, job_id)
    if job is None or job.status not in ("QUEUED", "PROCESSING"):
        return {}

    if job.job_type != "GEOPACKAGE_IMPORT":
        raise GeoPackageServiceError(
            f"Unsupported job type '{job.job_type}' for GeoPackage import.",
            code=GIS_IMPORT_FILE_INVALID,
        )

    geoai_job = session.get(GeoAIJob, job_id)
    if geoai_job is None:
        raise GeoPackageServiceError(
            "Linked GeoAI job record not found for GeoPackage import.",
            code=GIS_IMPORT_FILE_INVALID,
        )

    params = geoai_job.parameters_json or {}
    file_id_raw = params.get("file_id")
    if not file_id_raw:
        raise GeoPackageServiceError(
            "Missing 'file_id' in GeoPackage import parameters.",
            code=GIS_IMPORT_FILE_INVALID,
        )
    try:
        file_id = uuid.UUID(str(file_id_raw))
    except (ValueError, TypeError) as err:
        raise GeoPackageServiceError(
            "Invalid 'file_id' format in parameters.",
            code=GIS_IMPORT_FILE_INVALID,
        ) from err

    raw_mapping = params.get("layer_mapping")
    if not raw_mapping:
        raise GeoPackageServiceError(
            "Missing 'layer_mapping' in GeoPackage import parameters.",
            code=GIS_IMPORT_MAPPING_INVALID,
        )
    try:
        mapping_req = (
            LayerMappingRequest(**raw_mapping)
            if isinstance(raw_mapping, dict)
            else raw_mapping
        )
    except Exception as err:
        raise GeoPackageServiceError(
            f"Invalid layer mapping configuration: {err}",
            code=GIS_IMPORT_MAPPING_INVALID,
        ) from err

    import_mode = params.get("import_mode", "DRAFT_IMPORT")
    source_reference = params.get("source_reference")

    # Mark job as processing if queued
    if job.status == "QUEUED":
        mark_job_processing(session, job)
        record_audit(
            session,
            "geopackage.import_processing",
            "processing_job",
            job.id,
            project_id=job.project_id,
            metadata={"file_id": str(file_id)},
        )
        session.commit()

    try:
        # File isolation and category validation
        file_record = session.get(File, file_id)
        if file_record is None:
            raise GeoPackageServiceError(
                "Source file record was not found.",
                code=GIS_IMPORT_FILE_INVALID,
            )
        if file_record.project_id != job.project_id:
            raise GeoPackageServiceError(
                "File does not belong to the requested project.",
                code=GIS_IMPORT_FILE_INVALID,
            )
        if file_record.category != "GIS_IMPORT":
            raise GeoPackageServiceError(
                f"File category '{file_record.category}' is not valid for GIS_IMPORT.",
                code=GIS_IMPORT_FILE_INVALID,
            )

        storage = get_storage_service()
        with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as temp_file:
            temp_path = Path(temp_file.name)

        if hasattr(storage, "download_private_file") and getattr(storage, "__class__", None).__name__ == "PrivateObjectStorage":
            storage.download_private_file(file_record.storage_key, temp_path)
        else:
            file_bytes = storage.read_private_object(file_record.storage_key)
            temp_path.write_bytes(file_bytes)

        try:
            inspection = inspect_geopackage(temp_path)
            resolved_layers = validate_layer_mapping(inspection, mapping_req)

            layers_summary: dict[str, Any] = {}
            all_rejections: list[tuple[str, str]] = []
            warnings: list[str] = []

            for logical_key, layer_insp in resolved_layers.items():
                source_crs_info = detect_crs(layer_insp)
                expected_fam = EXPECTED_GEOMETRY_FAMILIES.get(logical_key, ())

                total_count = 0
                valid_count = 0
                rejected_count = 0
                repaired_count = 0
                imported_count = 0

                for _row_idx, raw_geom, _attrs in iter_layer_features(temp_path, layer_insp.name):
                    total_count += 1
                    if raw_geom is None:
                        rejected_count += 1
                        all_rejections.append((GIS_IMPORT_GEOMETRY_INVALID, "NULL_GEOMETRY"))
                        continue

                    # Feature-level CRS consistency check (F-15)
                    feat_srs_id = _attrs.get("_feature_srs_id")
                    target_source_crs: CRSInfo | CRS | str = source_crs_info
                    if feat_srs_id is not None and layer_insp.crs.srs_id is not None and feat_srs_id != layer_insp.crs.srs_id:
                        if feat_srs_id in (0, -1):
                            rejected_count += 1
                            all_rejections.append((GIS_IMPORT_CRS_MISSING, "FEATURE_CRS_UNDEFINED"))
                            continue
                        try:
                            feat_crs = CRS.from_epsg(feat_srs_id) if feat_srs_id > 0 else CRS.from_user_input(str(feat_srs_id))
                            target_source_crs = feat_crs
                        except Exception:
                            rejected_count += 1
                            all_rejections.append((GIS_IMPORT_CRS_MISSING, f"FEATURE_CRS_INCONSISTENT: {feat_srs_id}"))
                            continue

                    try:
                        transformed = transform_to_interchange_crs(raw_geom, target_source_crs)
                    except Exception as err:
                        rejected_count += 1
                        all_rejections.append((GIS_IMPORT_GEOMETRY_INVALID, f"TRANSFORM_FAILED: {err}"))
                        continue

                    valid_geom, was_repaired, err_code, reason = validate_geometry(
                        transformed, expected_fam
                    )
                    if err_code:
                        rejected_count += 1
                        all_rejections.append((err_code, reason or "GEOMETRY_INVALID"))
                        continue

                    valid_count += 1
                    if was_repaired:
                        repaired_count += 1
                        if reason and reason not in warnings:
                            warnings.append(reason)

                    # Persist valid feature to domain models
                    try:
                        attrs = _attrs or {}
                        now_utc = datetime.now(UTC)
                        src_ref = str(attrs.get("source_reference") or source_reference or file_record.original_name)
                        src_val = str(attrs.get("source") or "GIS_IMPORT")

                        raw_conf = attrs.get("confidence")
                        try:
                            conf = float(raw_conf) if raw_conf is not None else None
                        except (ValueError, TypeError):
                            conf = None

                        model_ver = str(attrs["model_version"]).strip() if attrs.get("model_version") else None

                        if logical_key == "parcels":
                            ext_id_raw = (
                                attrs.get("external_identifier")
                                or attrs.get("parcel_id")
                                or attrs.get("khasra_no")
                                or attrs.get("id")
                                or attrs.get("name")
                            )
                            ext_id = str(ext_id_raw).strip() if ext_id_raw is not None else None
                            ident_seed = ext_id if ext_id else str(_row_idx)
                            parcel_id = uuid.uuid5(NAMESPACE_GEOPACKAGE_IMPORT, f"parcel:{job.project_id}:{ident_seed}")
                            geom_version_id = uuid.uuid5(NAMESPACE_GEOPACKAGE_IMPORT, f"geom_version:{parcel_id}:1")

                            # Idempotency check: if already persisted, avoid duplicating
                            existing_parcel = session.get(Parcel, parcel_id)
                            if existing_parcel is None and hasattr(session, "records") and Parcel in session.records:
                                existing_parcel = session.records[Parcel].get(parcel_id)
                            if existing_parcel is not None:
                                imported_count += 1
                                continue

                            # Check for verified parcel conflict
                            is_verified = False
                            if ext_id:
                                if hasattr(session, "scalar"):
                                    v_parcel = session.scalar(
                                        select(Parcel).where(
                                            Parcel.project_id == job.project_id,
                                            Parcel.external_identifier == ext_id,
                                            Parcel.verification_status == "VERIFIED",
                                        )
                                    )
                                    if v_parcel is not None:
                                        is_verified = True
                                elif hasattr(session, "added"):
                                    for item in session.added:
                                        if (
                                            isinstance(item, Parcel)
                                            and item.project_id == job.project_id
                                            and item.external_identifier == ext_id
                                            and item.verification_status == "VERIFIED"
                                        ):
                                            is_verified = True
                                            break

                            if is_verified:
                                rejected_count += 1
                                all_rejections.append((
                                    GIS_IMPORT_GEOMETRY_INVALID,
                                    f"CANNOT_OVERWRITE_VERIFIED_PARCEL: {ext_id}",
                                ))
                                continue

                            # Area calculation
                            raw_area = attrs.get("area_m2") if attrs.get("area_m2") is not None else attrs.get("area")
                            try:
                                area_m2 = float(raw_area) if raw_area is not None else None
                            except (ValueError, TypeError):
                                area_m2 = None
                            if area_m2 is None or area_m2 <= 0:
                                area_m2, area_sqft = _calculate_geodesic_area_and_sqft(valid_geom)
                            else:
                                raw_sqft = attrs.get("area_sqft")
                                try:
                                    area_sqft = float(raw_sqft) if raw_sqft is not None else round(area_m2 * 10.7639104167, 2)
                                except (ValueError, TypeError):
                                    area_sqft = round(area_m2 * 10.7639104167, 2)

                            req_survey = bool(attrs.get("requires_survey", True))

                            parcel = Parcel(
                                id=parcel_id,
                                project_id=job.project_id,
                                external_identifier=ext_id,
                                source=src_val,
                                source_reference=src_ref,
                                status="DRAFT",
                                verification_status="UNVERIFIED",
                                current_geometry_version=1,
                                coordinate_space="WORLD",
                                source_crs=source_crs_info.crs_string,
                                confidence=conf,
                                model_version=model_ver,
                                requires_survey=req_survey,
                            )
                            version = ParcelGeometryVersion(
                                id=geom_version_id,
                                parcel_id=parcel_id,
                                version=1,
                                geometry=from_shape(valid_geom, srid=4326),
                                source_geometry_json=mapping(valid_geom),
                                source=src_val,
                                source_reference=src_ref,
                                coordinate_space="WORLD",
                                source_crs=source_crs_info.crs_string,
                                area_m2=area_m2,
                                area_sqft=area_sqft,
                                validation_status="VALID",
                                created_by_type="IMPORT",
                                processed_at=now_utc,
                            )
                            session.add(parcel)
                            session.add(version)
                            record_sync_change(
                                session,
                                project_id=job.project_id,
                                entity_type="PARCEL",
                                entity_id=parcel.id,
                                change_type="IMPORTED",
                                server_version=1,
                            )

                        elif logical_key == "buildings":
                            bldg_raw_id = attrs.get("building_id") or attrs.get("id") or attrs.get("name") or str(_row_idx)
                            bldg_id = uuid.uuid5(NAMESPACE_GEOPACKAGE_IMPORT, f"building:{job.project_id}:{file_record.id}:{bldg_raw_id}")
                            existing_bldg = session.get(Building, bldg_id)
                            if existing_bldg is None and hasattr(session, "records") and Building in session.records:
                                existing_bldg = session.records[Building].get(bldg_id)
                            if existing_bldg is not None:
                                imported_count += 1
                                continue

                            raw_area = attrs.get("area_m2") if attrs.get("area_m2") is not None else attrs.get("area")
                            try:
                                area_m2 = float(raw_area) if raw_area is not None else None
                            except (ValueError, TypeError):
                                area_m2 = None
                            if area_m2 is None or area_m2 <= 0:
                                area_m2, area_sqft = _calculate_geodesic_area_and_sqft(valid_geom)
                            else:
                                raw_sqft = attrs.get("area_sqft")
                                try:
                                    area_sqft = float(raw_sqft) if raw_sqft is not None else round(area_m2 * 10.7639104167, 2)
                                except (ValueError, TypeError):
                                    area_sqft = round(area_m2 * 10.7639104167, 2)

                            bldg = Building(
                                id=bldg_id,
                                project_id=job.project_id,
                                geometry=from_shape(valid_geom, srid=4326),
                                source=src_val,
                                source_reference=src_ref,
                                confidence=conf,
                                model_version=model_ver,
                                status="DETECTED",
                                verification_status="UNVERIFIED",
                                area_m2=area_m2,
                                area_sqft=area_sqft,
                                processed_at=now_utc,
                            )
                            session.add(bldg)

                        elif logical_key == "roads":
                            road_raw_id = attrs.get("road_id") or attrs.get("id") or attrs.get("name") or str(_row_idx)
                            road_id = uuid.uuid5(NAMESPACE_GEOPACKAGE_IMPORT, f"road:{job.project_id}:{file_record.id}:{road_raw_id}")
                            existing_road = session.get(Road, road_id)
                            if existing_road is None and hasattr(session, "records") and Road in session.records:
                                existing_road = session.records[Road].get(road_id)
                            if existing_road is not None:
                                imported_count += 1
                                continue

                            road_cls = str(
                                attrs.get("road_class")
                                or attrs.get("road_name")
                                or attrs.get("class")
                                or attrs.get("name")
                                or "UNCLASSIFIED"
                            )
                            raw_len = attrs.get("length_m") if attrs.get("length_m") is not None else attrs.get("length")
                            try:
                                length_m = float(raw_len) if raw_len is not None else None
                            except (ValueError, TypeError):
                                length_m = None
                            if length_m is None or length_m <= 0:
                                length_m = _calculate_geodesic_length_m(valid_geom)

                            road = Road(
                                id=road_id,
                                project_id=job.project_id,
                                geometry=from_shape(valid_geom, srid=4326),
                                road_class=road_cls,
                                source=src_val,
                                source_reference=src_ref,
                                confidence=conf,
                                model_version=model_ver,
                                status="ACTIVE",
                                verification_status="UNVERIFIED",
                                length_m=length_m,
                                processed_at=now_utc,
                            )
                            session.add(road)

                        elif logical_key == "land_use":
                            lu_raw_id = attrs.get("land_use_id") or attrs.get("id") or attrs.get("name") or str(_row_idx)
                            lu_id = uuid.uuid5(NAMESPACE_GEOPACKAGE_IMPORT, f"land_use:{job.project_id}:{file_record.id}:{lu_raw_id}")
                            existing_lu = session.get(LandUseFeature, lu_id)
                            if existing_lu is None and hasattr(session, "records") and LandUseFeature in session.records:
                                existing_lu = session.records[LandUseFeature].get(lu_id)
                            if existing_lu is not None:
                                imported_count += 1
                                continue

                            lu_cls = str(
                                attrs.get("land_use_class")
                                or attrs.get("class")
                                or attrs.get("use")
                                or attrs.get("name")
                                or "UNCLASSIFIED"
                            )
                            raw_area = attrs.get("area_m2") if attrs.get("area_m2") is not None else attrs.get("area")
                            try:
                                area_m2 = float(raw_area) if raw_area is not None else None
                            except (ValueError, TypeError):
                                area_m2 = None
                            if area_m2 is None or area_m2 <= 0:
                                area_m2, area_sqft = _calculate_geodesic_area_and_sqft(valid_geom)
                            else:
                                raw_sqft = attrs.get("area_sqft")
                                try:
                                    area_sqft = float(raw_sqft) if raw_sqft is not None else round(area_m2 * 10.7639104167, 2)
                                except (ValueError, TypeError):
                                    area_sqft = round(area_m2 * 10.7639104167, 2)

                            lu = LandUseFeature(
                                id=lu_id,
                                project_id=job.project_id,
                                geometry=from_shape(valid_geom, srid=4326),
                                land_use_class=lu_cls,
                                source=src_val,
                                source_reference=src_ref,
                                confidence=conf,
                                model_version=model_ver,
                                status="PROPOSED",
                                verification_status="UNVERIFIED",
                                area_m2=area_m2,
                                area_sqft=area_sqft,
                                processed_at=now_utc,
                            )
                            session.add(lu)

                        if hasattr(session, "flush"):
                            session.flush()
                        imported_count += 1

                    except Exception as persist_err:
                        rejected_count += 1
                        all_rejections.append((
                            GIS_IMPORT_GEOMETRY_INVALID,
                            f"PERSISTENCE_FAILED: {persist_err}",
                        ))

                layers_summary[logical_key] = GeoPackageImportLayerResult(
                    source_layer=layer_insp.name,
                    total=total_count,
                    valid=valid_count,
                    rejected=rejected_count,
                    repaired=repaired_count,
                    imported=imported_count,
                    source_crs=source_crs_info.crs_string,
                ).model_dump()

            bounded_rejections = bounded_rejection_summary(all_rejections)
            job_result = GeoPackageImportJobResult(
                kind="GEOPACKAGE_IMPORT",
                status="VALIDATED",
                project_id=str(job.project_id),
                file_id=str(file_record.id),
                source_reference=source_reference,
                import_mode=import_mode,
                layers={k: GeoPackageImportLayerResult(**v) for k, v in layers_summary.items()},
                warnings=warnings,
                rejection_summary=bounded_rejections,
            )

            geoai_job.metrics_json = {
                "layers": layers_summary,
                "rejection_summary": [s.model_dump() for s in bounded_rejections],
            }
            geoai_job.output_refs_json = job_result.model_dump()
            job.progress = 100
            mark_job_completed(session, job)

            # Sync linked GisImportRun if present
            if hasattr(session, "scalar"):
                import_run = session.scalar(
                    select(GisImportRun).where(GisImportRun.processing_job_id == job.id)
                )
                if import_run is not None:
                    sync_gis_import_run(session, import_run)

            record_audit(
                session,
                "geopackage.import_completed",
                "processing_job",
                job.id,
                project_id=job.project_id,
                metadata={
                    "layers": list(layers_summary.keys()),
                    "features_imported": sum(v["imported"] for v in layers_summary.values()),
                },
            )
            session.commit()
            return job_result.model_dump()
        finally:
            temp_path.unlink(missing_ok=True)

    except GeoPackageServiceError as err:
        session.rollback()
        job = session.get(ProcessingJob, job_id)
        geoai_job = session.get(GeoAIJob, job_id)
        if job is not None and job.status == "PROCESSING":
            job.status = "FAILED"
            job.error_json = {"code": err.code, "message": str(err), "details": err.details}
            if geoai_job is not None:
                geoai_job.metrics_json = {"error_code": err.code, "message": str(err)}
            record_audit(
                session,
                "geopackage.import_failed",
                "processing_job",
                job.id,
                project_id=job.project_id,
                metadata={"code": err.code, "reason": str(err)},
            )
            session.commit()
        raise
    except Exception:
        session.rollback()
        raise


def preview_geopackage_import(
    session: Session,
    *,
    project_id: uuid.UUID,
    file_record: File,
    mapping_req: LayerMappingRequest,
    source_reference: str | None = None,
) -> dict[str, Any]:
    """Execute dry-run inspection and validation of GeoPackage layers without modifying the database."""
    storage = get_storage_service()
    with tempfile.NamedTemporaryFile(suffix=".gpkg", delete=False) as temp_file:
        temp_path = Path(temp_file.name)

    if hasattr(storage, "download_private_file") and getattr(storage, "__class__", None).__name__ == "PrivateObjectStorage":
        storage.download_private_file(file_record.storage_key, temp_path)
    else:
        file_bytes = storage.read_private_object(file_record.storage_key)
        temp_path.write_bytes(file_bytes)

    try:
        inspection = inspect_geopackage(temp_path)
        resolved_layers = validate_layer_mapping(inspection, mapping_req)

        layers_summary: dict[str, Any] = {}
        all_rejections: list[tuple[str, str]] = []
        warnings: list[str] = []

        total_read = 0
        total_valid = 0
        total_rejected = 0
        total_repaired = 0

        for logical_key, layer_insp in resolved_layers.items():
            source_crs_info = detect_crs(layer_insp)
            expected_fam = EXPECTED_GEOMETRY_FAMILIES.get(logical_key, ())

            l_total = 0
            l_valid = 0
            l_rejected = 0
            l_repaired = 0

            for _row_idx, raw_geom, _attrs in iter_layer_features(temp_path, layer_insp.name):
                l_total += 1
                total_read += 1
                if raw_geom is None:
                    l_rejected += 1
                    total_rejected += 1
                    all_rejections.append((GIS_IMPORT_GEOMETRY_INVALID, "NULL_GEOMETRY"))
                    continue

                feat_srs_id = _attrs.get("_feature_srs_id")
                target_source_crs: CRSInfo | CRS | str = source_crs_info
                if feat_srs_id is not None and layer_insp.crs.srs_id is not None and feat_srs_id != layer_insp.crs.srs_id:
                    if feat_srs_id in (0, -1):
                        l_rejected += 1
                        total_rejected += 1
                        all_rejections.append((GIS_IMPORT_CRS_MISSING, "FEATURE_CRS_UNDEFINED"))
                        continue
                    try:
                        feat_crs = CRS.from_epsg(feat_srs_id) if feat_srs_id > 0 else CRS.from_user_input(str(feat_srs_id))
                        target_source_crs = feat_crs
                    except Exception:
                        l_rejected += 1
                        total_rejected += 1
                        all_rejections.append((GIS_IMPORT_CRS_MISSING, f"FEATURE_CRS_INCONSISTENT: {feat_srs_id}"))
                        continue

                try:
                    transformed = transform_to_interchange_crs(raw_geom, target_source_crs)
                except Exception as err:
                    l_rejected += 1
                    total_rejected += 1
                    all_rejections.append((GIS_IMPORT_GEOMETRY_INVALID, f"TRANSFORM_FAILED: {err}"))
                    continue

                valid_geom, was_repaired, err_code, reason = validate_geometry(transformed, expected_fam)
                if err_code:
                    l_rejected += 1
                    total_rejected += 1
                    all_rejections.append((err_code, reason or "GEOMETRY_INVALID"))
                    continue

                if was_repaired:
                    l_repaired += 1
                    total_repaired += 1
                    if reason and reason not in warnings:
                        warnings.append(reason)

                if logical_key == "parcels":
                    attrs = _attrs or {}
                    ext_id_raw = (
                        attrs.get("external_identifier")
                        or attrs.get("parcel_id")
                        or attrs.get("khasra_no")
                        or attrs.get("id")
                        or attrs.get("name")
                    )
                    ext_id = str(ext_id_raw).strip() if ext_id_raw is not None else None
                    if ext_id and hasattr(session, "scalar"):
                        v_parcel = session.scalar(
                            select(Parcel).where(
                                Parcel.project_id == project_id,
                                Parcel.external_identifier == ext_id,
                                Parcel.verification_status == "VERIFIED",
                            )
                        )
                        if v_parcel is not None:
                            l_rejected += 1
                            total_rejected += 1
                            all_rejections.append((
                                GIS_IMPORT_GEOMETRY_INVALID,
                                f"CANNOT_OVERWRITE_VERIFIED_PARCEL: {ext_id}",
                            ))
                            continue

                l_valid += 1
                total_valid += 1

            layers_summary[logical_key] = {
                "source_layer": layer_insp.name,
                "read": l_total,
                "valid": l_valid,
                "rejected": l_rejected,
                "repaired": l_repaired,
            }

        bounded_rejections = bounded_rejection_summary(all_rejections)

        return {
            "layers_detected": len(inspection.layers),
            "layers_mapped": len(resolved_layers),
            "features_read": total_read,
            "features_imported": total_valid,
            "features_rejected": total_rejected,
            "repairs_applied": total_repaired,
            "warnings": warnings,
            "rejection_summary": [s.model_dump() for s in bounded_rejections],
            "layer_details": layers_summary,
        }
    finally:
        temp_path.unlink(missing_ok=True)

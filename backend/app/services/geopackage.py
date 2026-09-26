"""GeoPackage GIS interchange service core: inspection, mapping, CRS, and geometry validation."""

from __future__ import annotations

import math
import sqlite3
import struct
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.geometry import GeometryCollection, LineString, MultiLineString, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry
from shapely.ops import unary_union

from app.schemas.geopackage import (
    EXPECTED_GEOMETRY_FAMILIES,
    GIS_IMPORT_CRS_MISSING,
    GIS_IMPORT_FILE_INVALID,
    GIS_IMPORT_GEOMETRY_INVALID,
    GIS_IMPORT_LAYER_NOT_FOUND,
    GIS_IMPORT_MAPPING_INVALID,
    CRSInfo,
    GeoPackageInspection,
    GeometryValidationResult,
    LayerInspection,
    LayerMappingRequest,
    LogicalLayer,
    RejectionSummary,
)


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
            if geom_blob is not None:
                try:
                    _, geom = unpack_gpkg_geometry(geom_blob)
                except Exception:
                    geom = None
            yield row_idx, geom, row_dict
            row_idx += 1
    finally:
        conn.close()

"""Input validation and CRS-safe measurements for C.5 GIS features."""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.errors import GEOSException
from shapely.geometry import LineString, MultiLineString, MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry

from ai.geoai.polygonization.geometry import crs_to_string


class FeatureValidationError(ValueError):
    """Raised when supplied source geometry cannot form a C.5 feature."""


def load_json_input(value: str | Path) -> dict[str, Any]:
    """Load a JSON/GeoJSON file path or a literal JSON object without mutation."""
    candidate = Path(value)
    try:
        raw = candidate.read_text(encoding="utf-8") if candidate.exists() else str(value)
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as error:
        raise FeatureValidationError("Input must be a readable JSON/GeoJSON file or literal JSON object.") from error
    if not isinstance(payload, dict):
        raise FeatureValidationError("Input JSON must be an object.")
    return copy.deepcopy(payload)


def geometry_from_payload(payload: dict[str, Any], *, expected: str) -> tuple[BaseGeometry, dict[str, Any]]:
    """Extract one GeoJSON geometry, accepting a single-feature collection only."""
    source = copy.deepcopy(payload)
    metadata: dict[str, Any] = {}
    if source.get("type") == "Feature":
        geometry_data = source.get("geometry")
        if isinstance(source.get("properties"), dict):
            metadata = copy.deepcopy(source["properties"])
    elif source.get("type") == "FeatureCollection":
        features = source.get("features")
        if not isinstance(features, list) or len(features) != 1:
            raise FeatureValidationError("FeatureCollection input must contain exactly one feature.")
        feature = features[0]
        if not isinstance(feature, dict):
            raise FeatureValidationError("FeatureCollection contains an invalid feature.")
        geometry_data = feature.get("geometry")
        if isinstance(feature.get("properties"), dict):
            metadata = copy.deepcopy(feature["properties"])
    elif "type" in source and "coordinates" in source:
        geometry_data = source
    elif "coordinates" in source:
        geometry_data = {"type": expected, "coordinates": source["coordinates"]}
    else:
        raise FeatureValidationError("Input must contain GeoJSON geometry or coordinates.")
    if not isinstance(geometry_data, dict):
        raise FeatureValidationError("Input geometry is required.")
    try:
        geometry = shape(geometry_data)
    except (GEOSException, TypeError, ValueError) as error:
        # Shapely may raise a GEOSException for malformed coordinate sequences.
        raise FeatureValidationError("Input geometry is not valid GeoJSON; road lines need at least two distinct coordinates.") from error
    if geometry.is_empty:
        raise FeatureValidationError("Input geometry must not be empty.")
    source.update(metadata)
    return geometry, source


def coordinate_context(payload: dict[str, Any], source_crs: str | None) -> tuple[str, str | None]:
    """Resolve declared coordinate context while refusing to invent a CRS."""
    declared_space = payload.get("coordinate_space")
    declared_crs = source_crs if source_crs is not None else payload.get("source_crs")
    if declared_crs is not None:
        if not isinstance(declared_crs, str):
            raise FeatureValidationError("source_crs must be a CRS string.")
        try:
            return "WORLD", crs_to_string(declared_crs)
        except Exception as error:
            raise FeatureValidationError(f"Invalid source CRS: {declared_crs!r}.") from error
    if declared_space in {"PIXEL", "LOCAL", "UNKNOWN"}:
        return declared_space, None
    return "LOCAL", None


def validated_line_geometry(
    geometry: BaseGeometry, *, allow_multiline: bool, allow_surface: bool
) -> tuple[BaseGeometry, str]:
    """Validate linework and explicitly-declared polygon road surfaces."""
    if isinstance(geometry, MultiLineString) and not allow_multiline:
        raise FeatureValidationError("MultiLineString requires allow_multiline=True.")
    if isinstance(geometry, (LineString, MultiLineString)):
        lines = geometry.geoms if isinstance(geometry, MultiLineString) else (geometry,)
        for line in lines:
            if len(line.coords) < 2 or len({tuple(point) for point in line.coords}) < 2:
                raise FeatureValidationError("A road LineString needs at least two distinct coordinates.")
        if not geometry.is_valid:
            raise FeatureValidationError("Road line geometry is invalid.")
        return geometry, "LINE"
    if allow_surface and isinstance(geometry, (Polygon, MultiPolygon)):
        candidate = make_valid(geometry) if not geometry.is_valid else geometry
        if candidate.is_empty or not isinstance(candidate, (Polygon, MultiPolygon)):
            raise FeatureValidationError("Road surface geometry is invalid.")
        return candidate, "SURFACE"
    raise FeatureValidationError("Road geometry must be a LineString, supported MultiLineString, or explicitly declared polygon surface.")


def validated_polygon_geometry(geometry: BaseGeometry, *, allow_multipolygon: bool) -> Polygon | MultiPolygon:
    """Validate a polygon without changing a valid source geometry."""
    if isinstance(geometry, MultiPolygon) and not allow_multipolygon:
        raise FeatureValidationError("MultiPolygon requires allow_multipolygon=True.")
    if not isinstance(geometry, (Polygon, MultiPolygon)):
        raise FeatureValidationError("Land-use geometry must be a Polygon or supported MultiPolygon.")
    candidate = make_valid(geometry) if not geometry.is_valid else geometry
    if candidate.is_empty or not isinstance(candidate, (Polygon, MultiPolygon)):
        raise FeatureValidationError("Land-use polygon geometry is invalid.")
    return candidate


def length_metres(geometry: BaseGeometry, source_crs: str | None) -> float | None:
    """Measure linework in metres, using geodesic calculations for geographic CRSs."""
    if source_crs is None or geometry.is_empty:
        return None
    parsed = CRS.from_user_input(source_crs)
    linework = geometry.boundary if isinstance(geometry, (Polygon, MultiPolygon)) else geometry
    if parsed.is_geographic:
        geod = parsed.get_geod()
        lines = linework.geoms if isinstance(linework, MultiLineString) else (linework,)
        return float(sum(geod.line_length(*zip(*line.coords)) for line in lines))
    if parsed.is_projected and len(parsed.axis_info) >= 2:
        factors = [axis.unit_conversion_factor for axis in parsed.axis_info[:2]]
        if all(math.isfinite(value) and value > 0 for value in factors):
            return float(linework.length * sum(factors) / len(factors))
    transformer = Transformer.from_crs(parsed, "EPSG:6933", always_xy=True)
    return float(transform_geometry(transformer.transform, linework).length)

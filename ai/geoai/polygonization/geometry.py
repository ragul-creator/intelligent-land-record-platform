"""Conservative geometry and CRS-safe area helpers for preliminary GeoAI features."""

from __future__ import annotations

import math

from pyproj import CRS, Transformer
from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry
from shapely.ops import unary_union


SQUARE_FEET_PER_SQUARE_METRE = 10.7639104167


def clean_polygon(geometry: BaseGeometry, *, simplify_tolerance: float = 0.0) -> Polygon | MultiPolygon | None:
    """Repair one raster component conservatively without rectangularizing it."""
    if simplify_tolerance < 0:
        raise ValueError("simplify_tolerance must be zero or greater.")
    candidate = geometry
    if not candidate.is_valid:
        candidate = make_valid(candidate)
    if simplify_tolerance:
        candidate = candidate.simplify(simplify_tolerance, preserve_topology=True)
    if not candidate.is_valid:
        candidate = make_valid(candidate)
    if candidate.is_empty:
        return None
    if isinstance(candidate, (Polygon, MultiPolygon)):
        return candidate
    if isinstance(candidate, GeometryCollection):
        polygons = [part for part in candidate.geoms if isinstance(part, (Polygon, MultiPolygon)) and not part.is_empty]
        if polygons:
            repaired = unary_union(polygons)
            if isinstance(repaired, (Polygon, MultiPolygon)) and not repaired.is_empty:
                return repaired
    return None


def crs_to_string(crs: CRS | str | object | None) -> str | None:
    """Normalize a supplied raster CRS to a stable, human-readable representation."""
    return CRS.from_user_input(crs).to_string() if crs is not None else None


def area_square_metres(geometry: BaseGeometry, crs: CRS | str | object | None) -> float | None:
    """Return a real-world area without ever treating degree-squared as square metres."""
    if crs is None or geometry.is_empty:
        return None
    parsed = CRS.from_user_input(crs)
    if parsed.is_geographic:
        area, _ = parsed.get_geod().geometry_area_perimeter(geometry)
        return abs(float(area))
    if parsed.is_projected:
        axis_info = parsed.axis_info
        if len(axis_info) >= 2:
            x_factor = axis_info[0].unit_conversion_factor
            y_factor = axis_info[1].unit_conversion_factor
            if all(math.isfinite(value) and value > 0 for value in (x_factor, y_factor)):
                return float(geometry.area * x_factor * y_factor)
    equal_area = Transformer.from_crs(parsed, "EPSG:6933", always_xy=True)
    return float(transform_geometry(equal_area.transform, geometry).area)


def area_square_feet(area_m2: float | None) -> float | None:
    """Convert a valid square-metre result using the approved constant."""
    return None if area_m2 is None else area_m2 * SQUARE_FEET_PER_SQUARE_METRE


def to_wgs84(geometry: BaseGeometry, source_crs: CRS | str | object) -> BaseGeometry:
    """Transform a world-coordinate geometry for standards-oriented GeoJSON export."""
    parsed = CRS.from_user_input(source_crs)
    if parsed.to_epsg() == 4326:
        return geometry
    transformer = Transformer.from_crs(parsed, "EPSG:4326", always_xy=True)
    return transform_geometry(transformer.transform, geometry)

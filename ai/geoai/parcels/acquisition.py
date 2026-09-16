"""Acquire source-backed preliminary parcels without inferring invisible legal boundaries."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from pyproj import CRS
from shapely.geometry import MultiPolygon, Polygon, shape
from shapely.geometry.base import BaseGeometry

from ai.geoai.parcels.models import EvidenceType, ParcelResult, ParcelSource
from ai.geoai.polygonization.geometry import area_square_feet, area_square_metres, clean_polygon, crs_to_string


class ParcelValidationError(ValueError):
    """Caller-safe validation failure for a source parcel candidate."""


def load_json_input(value: str | Path) -> dict[str, object]:
    """Load a JSON/GeoJSON file or literal JSON without changing source input."""
    candidate = Path(value)
    text = candidate.read_text(encoding="utf-8") if candidate.is_file() else str(value)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ParcelValidationError("Parcel input must be a JSON object or GeoJSON object.")
    return parsed


def _source_crs(payload: dict[str, object], override: str | None) -> str | None:
    if override is not None:
        return crs_to_string(override)
    candidate = payload.get("source_crs") or payload.get("crs")
    if isinstance(candidate, dict):
        candidate = (candidate.get("properties") or {}).get("name")
    return crs_to_string(candidate) if isinstance(candidate, str) else None


def _coordinate_space(payload: dict[str, object], source_crs: str | None) -> str:
    if source_crs is not None:
        return "WORLD"
    requested = payload.get("coordinate_space", "LOCAL")
    if requested not in {"PIXEL", "LOCAL", "UNKNOWN"}:
        raise ParcelValidationError("Coordinates without a CRS must declare PIXEL, LOCAL, or UNKNOWN coordinate_space.")
    return str(requested)


def _geometry_mapping(payload: dict[str, object]) -> dict[str, object]:
    candidate: object = payload
    if payload.get("type") == "Feature":
        candidate = payload.get("geometry")
    elif payload.get("type") == "FeatureCollection":
        features = payload.get("features")
        if not isinstance(features, list) or len(features) != 1:
            raise ParcelValidationError("A parcel FeatureCollection input must contain exactly one feature.")
        candidate = features[0].get("geometry") if isinstance(features[0], dict) else None
    elif "geometry" in payload:
        candidate = payload.get("geometry")
    elif "coordinates" in payload:
        candidate = {"type": "Polygon", "coordinates": payload["coordinates"]}
    if not isinstance(candidate, dict) or candidate.get("type") not in {"Polygon", "MultiPolygon"}:
        raise ParcelValidationError("Parcel geometry must be a GeoJSON Polygon or explicitly allowed MultiPolygon.")
    return deepcopy(candidate)


def _fmb_import_geometry(payload: dict[str, object]) -> dict[str, object]:
    """MVP adapter for already-digitized/georeferenced FMB geometry only."""
    return _geometry_mapping(payload)


def _distinct_vertex_count(geometry: Polygon | MultiPolygon) -> int:
    if isinstance(geometry, Polygon):
        return len({(float(x), float(y)) for x, y, *_ in geometry.exterior.coords[:-1]})
    return sum(_distinct_vertex_count(part) for part in geometry.geoms)


def _validated_geometry(mapping: dict[str, object], *, allow_multipolygon: bool) -> tuple[Polygon | MultiPolygon, tuple[str, ...]]:
    original = shape(mapping)
    if not isinstance(original, (Polygon, MultiPolygon)):
        raise ParcelValidationError("Parcel geometry must be polygonal.")
    if isinstance(original, MultiPolygon) and not allow_multipolygon:
        raise ParcelValidationError("MultiPolygon parcels require --allow-multipolygon.")
    if _distinct_vertex_count(original) < 3:
        raise ParcelValidationError("Parcel polygon requires at least three distinct vertices.")
    warnings: tuple[str, ...] = ()
    repaired = clean_polygon(original)
    if repaired is None or repaired.is_empty or not repaired.is_valid:
        raise ParcelValidationError("Parcel geometry is invalid and cannot be safely repaired.")
    if isinstance(repaired, MultiPolygon) and not allow_multipolygon:
        raise ParcelValidationError("Parcel repair produced a MultiPolygon; rerun only when --allow-multipolygon is appropriate.")
    if _distinct_vertex_count(repaired) < 3 or repaired.area <= 0:
        raise ParcelValidationError("Parcel geometry must be closed and have non-zero area.")
    if not original.is_valid:
        warnings = ("Input geometry was conservatively repaired; review before approval.",)
    return repaired, warnings


def _survey_points(payload: dict[str, object]) -> tuple[tuple[float, float], ...]:
    candidate = payload.get("points") or payload.get("coordinates")
    if isinstance(candidate, list) and candidate and isinstance(candidate[0], list) and candidate[0] and isinstance(candidate[0][0], list):
        candidate = candidate[0]
    if not isinstance(candidate, list):
        raise ParcelValidationError("GNSS_SURVEY input requires ordered points or coordinates.")
    try:
        points = tuple((float(point[0]), float(point[1])) for point in candidate)
    except (IndexError, TypeError, ValueError) as error:
        raise ParcelValidationError("GNSS_SURVEY points must contain numeric x/y coordinate pairs.") from error
    if len(set(points)) < 3:
        raise ParcelValidationError("GNSS_SURVEY requires at least three distinct ordered corner points.")
    return points


def _confidence(payload: dict[str, object]) -> float | None:
    value = payload.get("confidence")
    if value is None:
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError) as error:
        raise ParcelValidationError("confidence must be numeric when supplied.") from error
    if not 0.0 <= confidence <= 1.0:
        raise ParcelValidationError("confidence must be between 0 and 1.")
    return confidence


def _string_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise ParcelValidationError("notes must be a string or a list of strings.")


def _model_version(payload: dict[str, object], override: str | None) -> str | None:
    if override is not None:
        return override
    value = payload.get("model_version")
    if value is None or isinstance(value, str):
        return value
    raise ParcelValidationError("model_version must be a string when supplied.")


def create_parcel(
    source_type: ParcelSource | str,
    payload: dict[str, object],
    *,
    source_crs: str | None = None,
    source_reference: str | None = None,
    allow_multipolygon: bool = False,
    model_version: str | None = None,
) -> ParcelResult:
    """Create one draft parcel from declared spatial evidence, never from absent evidence."""
    source = ParcelSource(source_type)
    source_crs_value = _source_crs(payload, source_crs)
    coordinate_space = _coordinate_space(payload, source_crs_value)
    processed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    parcel_id = str(payload.get("parcel_id") or uuid4())
    reference = source_reference if source_reference is not None else payload.get("source_reference")
    if reference is not None and not isinstance(reference, str):
        raise ParcelValidationError("source_reference must be a string when supplied.")
    confidence = _confidence(payload)
    notes = _string_tuple(payload.get("notes"))
    evidence_type = EvidenceType(payload["evidence_type"]) if source is ParcelSource.AI_VISIBLE_BOUNDARY and "evidence_type" in payload else None
    if source is ParcelSource.AI_VISIBLE_BOUNDARY and evidence_type is None:
        raise ParcelValidationError("AI_VISIBLE_BOUNDARY requires an evidence_type.")
    if evidence_type is EvidenceType.NO_VISIBLE_EVIDENCE:
        return ParcelResult(
            parcel_id=parcel_id,
            source=source,
            source_reference=reference,
            coordinate_space=coordinate_space,
            source_crs=source_crs_value,
            geometry=None,
            status="NOT_DETERMINED",
            verification_status="UNVERIFIED",
            geometry_version=1,
            confidence=confidence,
            evidence_type=evidence_type,
            model_version=_model_version(payload, model_version),
            processed_at=processed_at,
            notes=notes,
            warnings=("No visible boundary evidence was supplied; imagery alone cannot determine a legal parcel boundary.",),
            requires_survey=True,
            area_m2=None,
            area_sqft=None,
            ai_boundary_status="AI_PRELIMINARY",
        )
    survey_points: tuple[tuple[float, float], ...] | None = None
    if source is ParcelSource.GNSS_SURVEY:
        survey_points = _survey_points(payload)
        geometry_mapping: dict[str, object] = {"type": "Polygon", "coordinates": [list(survey_points)]}
    elif source is ParcelSource.FMB_IMPORT:
        geometry_mapping = _fmb_import_geometry(payload)
    else:
        geometry_mapping = _geometry_mapping(payload)
    geometry, warnings = _validated_geometry(geometry_mapping, allow_multipolygon=allow_multipolygon)
    area_m2 = area_square_metres(geometry, source_crs_value) if coordinate_space == "WORLD" else None
    requires_survey = source is ParcelSource.AI_VISIBLE_BOUNDARY
    if source is ParcelSource.AI_VISIBLE_BOUNDARY:
        warnings += ("Visible-boundary evidence is not a legal parcel boundary and requires GIS/survey verification.",)
    return ParcelResult(
        parcel_id=parcel_id,
        source=source,
        source_reference=reference,
        coordinate_space=coordinate_space,
        source_crs=source_crs_value,
        geometry=geometry,
        status="DRAFT",
        verification_status="UNVERIFIED",
        geometry_version=1,
        confidence=confidence,
        evidence_type=evidence_type,
        model_version=_model_version(payload, model_version),
        processed_at=processed_at,
        notes=notes,
        warnings=warnings,
        requires_survey=requires_survey,
        area_m2=area_m2,
        area_sqft=area_square_feet(area_m2),
        survey_points=survey_points,
        ai_boundary_status="AI_PRELIMINARY" if source is ParcelSource.AI_VISIBLE_BOUNDARY else None,
    )

"""C.7 PostGIS persistence helpers; reuse GeoAI acquisition and validation modules."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from geoalchemy2.shape import from_shape, to_shape
from pyproj import CRS, Transformer
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry
from sqlalchemy import select
from sqlalchemy.orm import Session

from ai.geoai.parcels.acquisition import ParcelValidationError, create_parcel
from ai.geoai.topology import TopologyIssue, validate_edited_parcel
from app.models import Parcel, ParcelGeometryVersion, TopologyError


class GeoAIServiceError(ValueError):
    """Safe domain error for rejected GeoAI persistence requests."""


class ParcelVersionConflict(GeoAIServiceError):
    """The draft was based on a geometry version that is no longer current."""


def _as_utc(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _to_wgs84(geometry: BaseGeometry, source_crs: str) -> BaseGeometry:
    source = CRS.from_user_input(source_crs)
    if source.to_epsg() == 4326:
        return geometry
    return transform_geometry(Transformer.from_crs(source, "EPSG:4326", always_xy=True).transform, geometry)


def _from_wgs84(geometry: BaseGeometry, target_crs: str) -> BaseGeometry:
    target = CRS.from_user_input(target_crs)
    if target.to_epsg() == 4326:
        return geometry
    return transform_geometry(Transformer.from_crs("EPSG:4326", target, always_xy=True).transform, geometry)


def geometry_geojson(value: Any | None) -> dict[str, Any] | None:
    return mapping(to_shape(value)) if value is not None else None


def persist_parcel_import(session: Session, project_id: uuid.UUID, parameters: dict[str, Any]) -> Parcel:
    """Run C.4 acquisition and persist parcel/version 1 without overwriting source evidence."""
    try:
        result = create_parcel(
            parameters["source_type"],
            parameters["source_payload"],
            source_crs=parameters.get("source_crs"),
            source_reference=parameters.get("source_reference"),
            model_version=parameters.get("model_version"),
            allow_multipolygon=bool(parameters.get("allow_multipolygon")),
        )
    except (KeyError, ParcelValidationError, ValueError) as error:
        raise GeoAIServiceError("Parcel import input is invalid.") from error

    stored_geometry = None
    if result.geometry is not None and result.coordinate_space == "WORLD":
        if result.source_crs is None:
            raise GeoAIServiceError("World parcel geometry requires a source CRS.")
        stored_geometry = from_shape(_to_wgs84(result.geometry, result.source_crs), srid=4326)
    parcel = Parcel(
        project_id=project_id,
        external_identifier=result.parcel_id,
        source=result.source.value,
        source_reference=result.source_reference,
        status=result.status,
        verification_status=result.verification_status,
        current_geometry_version=1,
        coordinate_space=result.coordinate_space,
        source_crs=result.source_crs,
        confidence=result.confidence,
        model_version=result.model_version,
        ai_boundary_status=result.ai_boundary_status,
        requires_survey=result.requires_survey,
    )
    session.add(parcel)
    session.flush()
    session.add(
        ParcelGeometryVersion(
            parcel_id=parcel.id,
            version=1,
            geometry=stored_geometry,
            source_geometry_json=parameters["source_payload"],
            source=result.source.value,
            source_reference=result.source_reference,
            coordinate_space=result.coordinate_space,
            source_crs=result.source_crs,
            area_m2=result.area_m2,
            area_sqft=result.area_sqft,
            validation_status="NOT_DETERMINED" if result.geometry is None else "VALID",
            created_by_type="AI" if result.ai_boundary_status else "IMPORT",
            processed_at=_as_utc(result.processed_at),
        )
    )
    return parcel


def current_version(session: Session, parcel: Parcel) -> ParcelGeometryVersion:
    version = session.scalar(
        select(ParcelGeometryVersion).where(
            ParcelGeometryVersion.parcel_id == parcel.id,
            ParcelGeometryVersion.version == parcel.current_geometry_version,
        )
    )
    if version is None:
        raise GeoAIServiceError("Parcel geometry history is incomplete.")
    return version


def _persist_topology_issues(session: Session, project_id: uuid.UUID, parcel_id: uuid.UUID, issues: tuple[TopologyIssue, ...]) -> None:
    for issue in issues:
        related_id = None
        if len(issue.parcel_ids) > 1:
            try:
                related_id = uuid.UUID(issue.parcel_ids[1])
            except ValueError:
                pass
        session.add(TopologyError(project_id=project_id, parcel_id=parcel_id, related_parcel_id=related_id, code=issue.code, severity=issue.severity, area_m2=issue.area_m2, message=issue.message))


def create_human_parcel_version(
    session: Session,
    parcel: Parcel,
    user_id: uuid.UUID,
    geometry_data: dict[str, Any],
    source_crs: str,
    expected_current_version: int,
    change_reason: str | None,
) -> tuple[ParcelGeometryVersion, Any]:
    """Append a validated human version atomically; no prior version is modified."""
    locked = session.scalar(select(Parcel).where(Parcel.id == parcel.id).with_for_update())
    if locked is None:
        raise GeoAIServiceError("Parcel was not found.")
    if locked.current_geometry_version != expected_current_version:
        raise ParcelVersionConflict("This parcel has a newer geometry version. Refresh it before saving your draft.")
    previous = current_version(session, locked)
    if previous.geometry is None:
        raise GeoAIServiceError("A parcel without world geometry cannot be edited until it is georeferenced.")
    try:
        edited = shape(geometry_data)
        original = _from_wgs84(to_shape(previous.geometry), source_crs)
    except (TypeError, ValueError) as error:
        raise GeoAIServiceError("Edited geometry or source CRS is invalid.") from error
    neighbours = {
        str(version.parcel_id): _from_wgs84(to_shape(version.geometry), source_crs)
        for version in session.scalars(
            select(ParcelGeometryVersion)
            .join(Parcel, Parcel.id == ParcelGeometryVersion.parcel_id)
            .where(Parcel.project_id == locked.project_id, Parcel.id != locked.id, ParcelGeometryVersion.version == Parcel.current_geometry_version, ParcelGeometryVersion.geometry.is_not(None))
        )
    }
    result = validate_edited_parcel(original, edited, parcel_id=str(locked.id), source_crs=source_crs, neighbouring_parcels=neighbours)
    if result.status == "INVALID" or result.geometry is None:
        raise GeoAIServiceError("Edited parcel geometry is invalid.")
    next_version = locked.current_geometry_version + 1
    persisted = ParcelGeometryVersion(
        parcel_id=locked.id,
        version=next_version,
        geometry=from_shape(_to_wgs84(result.geometry, source_crs), srid=4326),
        source_geometry_json=geometry_data,
        source="HUMAN_DRAWN",
        coordinate_space="WORLD",
        source_crs=CRS.from_user_input(source_crs).to_string(),
        area_m2=result.area_after_m2,
        area_sqft=None if result.area_after_m2 is None else result.area_after_m2 * 10.7639104167,
        change_reason=change_reason,
        validation_status=result.status,
        created_by_user_id=user_id,
        created_by_type="HUMAN",
    )
    session.add(persisted)
    locked.current_geometry_version = next_version
    locked.status = "REVIEW_REQUIRED" if result.status == "REVIEW_REQUIRED" else "DRAFT"
    _persist_topology_issues(session, locked.project_id, locked.id, result.issues)
    return persisted, result

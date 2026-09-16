"""Create source-backed C.5 road, pathway, and access-corridor features."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from ai.geoai.features.schemas import FeatureSource, RoadClass, RoadFeatureResult
from ai.geoai.features.validation import coordinate_context, geometry_from_payload, length_metres, validated_line_geometry


def create_road(
    source_type: FeatureSource | str,
    payload: dict[str, object],
    road_class: RoadClass | str,
    *,
    source_crs: str | None = None,
    source_reference: str | None = None,
    allow_multiline: bool = False,
    model_version: str | None = None,
) -> RoadFeatureResult:
    """Create a DRAFT/UNVERIFIED road feature from declared source geometry."""
    source = FeatureSource(source_type)
    road_kind = RoadClass(road_class)
    geometry, source_payload = geometry_from_payload(payload, expected="LineString")
    geometry, geometry_kind = validated_line_geometry(
        geometry,
        allow_multiline=allow_multiline,
        allow_surface=bool(source_payload.get("road_surface")),
    )
    coordinate_space, normalized_crs = coordinate_context(source_payload, source_crs)
    confidence = source_payload.get("confidence")
    if confidence is not None:
        confidence = float(confidence)
    notes = tuple(str(note) for note in source_payload.get("notes", []))
    warnings = tuple(str(warning) for warning in source_payload.get("warnings", []))
    ai_status = "AI_PRELIMINARY" if source is FeatureSource.AI_CANDIDATE else None
    if ai_status:
        warnings += ("AI-derived road/pathway candidates require human GIS or survey verification.",)
    return RoadFeatureResult(
        feature_id=str(uuid4()),
        feature_type="ROAD",
        road_class=road_kind,
        source=source,
        source_reference=source_reference,
        coordinate_space=coordinate_space,
        source_crs=normalized_crs,
        geometry=geometry,
        geometry_kind=geometry_kind,
        status="DRAFT",
        verification_status="UNVERIFIED",
        confidence=confidence,
        model_version=model_version,
        processed_at=datetime.now(timezone.utc).isoformat(),
        notes=notes,
        warnings=warnings,
        length_m=length_metres(geometry, normalized_crs) if coordinate_space == "WORLD" else None,
        ai_status=ai_status,
    )

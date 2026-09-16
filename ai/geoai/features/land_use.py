"""Create source-backed C.5 preliminary land-use GIS features."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from ai.geoai.features.schemas import FeatureSource, LandUseClass, LandUseFeatureResult
from ai.geoai.features.validation import coordinate_context, geometry_from_payload, validated_polygon_geometry
from ai.geoai.polygonization.geometry import area_square_feet, area_square_metres


def create_land_use(
    source_type: FeatureSource | str,
    payload: dict[str, object],
    land_use_class: LandUseClass | str,
    *,
    source_crs: str | None = None,
    source_reference: str | None = None,
    allow_multipolygon: bool = False,
    model_version: str | None = None,
) -> LandUseFeatureResult:
    """Create a DRAFT/UNVERIFIED land-use feature without legal classification claims."""
    source = FeatureSource(source_type)
    land_use = LandUseClass(land_use_class)
    geometry, source_payload = geometry_from_payload(payload, expected="Polygon")
    geometry = validated_polygon_geometry(geometry, allow_multipolygon=allow_multipolygon)
    coordinate_space, normalized_crs = coordinate_context(source_payload, source_crs)
    confidence = source_payload.get("confidence")
    if confidence is not None:
        confidence = float(confidence)
    notes = tuple(str(note) for note in source_payload.get("notes", []))
    warnings = tuple(str(warning) for warning in source_payload.get("warnings", []))
    ai_status = "AI_PRELIMINARY" if source is FeatureSource.AI_CANDIDATE else None
    if ai_status:
        warnings += ("Imagery-derived land-use classification is preliminary and not a statutory or legal classification.",)
    area_m2 = area_square_metres(geometry, normalized_crs) if coordinate_space == "WORLD" else None
    return LandUseFeatureResult(
        feature_id=str(uuid4()),
        feature_type="LAND_USE",
        land_use_class=land_use,
        source=source,
        source_reference=source_reference,
        coordinate_space=coordinate_space,
        source_crs=normalized_crs,
        geometry=geometry,
        status="DRAFT",
        verification_status="UNVERIFIED",
        confidence=confidence,
        model_version=model_version,
        processed_at=datetime.now(timezone.utc).isoformat(),
        notes=notes,
        warnings=warnings,
        area_m2=area_m2,
        area_sqft=area_square_feet(area_m2),
        ai_status=ai_status,
    )

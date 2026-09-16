"""Stable C.5 GIS feature types suitable for later PostGIS integration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from shapely.geometry.base import BaseGeometry


class FeatureSource(str, Enum):
    EXISTING_GIS = "EXISTING_GIS"
    MANUAL_DRAWN = "MANUAL_DRAWN"
    AI_CANDIDATE = "AI_CANDIDATE"


class RoadClass(str, Enum):
    ROAD = "ROAD"
    PATHWAY = "PATHWAY"
    ACCESS_CORRIDOR = "ACCESS_CORRIDOR"


class LandUseClass(str, Enum):
    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL = "COMMERCIAL"
    INDUSTRIAL = "INDUSTRIAL"
    AGRICULTURAL = "AGRICULTURAL"
    VACANT = "VACANT"
    ROAD_TRANSPORT = "ROAD_TRANSPORT"
    WATERBODY = "WATERBODY"
    GREEN_OPEN_SPACE = "GREEN_OPEN_SPACE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RoadFeatureResult:
    feature_id: str
    feature_type: str
    road_class: RoadClass
    source: FeatureSource
    source_reference: str | None
    coordinate_space: str
    source_crs: str | None
    geometry: BaseGeometry
    geometry_kind: str
    status: str
    verification_status: str
    confidence: float | None
    model_version: str | None
    processed_at: str
    notes: tuple[str, ...]
    warnings: tuple[str, ...]
    length_m: float | None
    ai_status: str | None = None

    def properties(self, *, exported_crs: str | None = None) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "feature_type": self.feature_type,
            "road_class": self.road_class.value,
            "source": self.source.value,
            "source_reference": self.source_reference,
            "coordinate_space": self.coordinate_space,
            "source_crs": self.source_crs,
            "crs": exported_crs if exported_crs is not None else self.source_crs,
            "geometry_kind": self.geometry_kind,
            "status": self.status,
            "verification_status": self.verification_status,
            "confidence": self.confidence,
            "model_version": self.model_version,
            "processed_at": self.processed_at,
            "notes": list(self.notes),
            "warnings": list(self.warnings),
            "length_m": self.length_m,
            "ai_status": self.ai_status,
        }


@dataclass(frozen=True)
class LandUseFeatureResult:
    feature_id: str
    feature_type: str
    land_use_class: LandUseClass
    source: FeatureSource
    source_reference: str | None
    coordinate_space: str
    source_crs: str | None
    geometry: BaseGeometry
    status: str
    verification_status: str
    confidence: float | None
    model_version: str | None
    processed_at: str
    notes: tuple[str, ...]
    warnings: tuple[str, ...]
    area_m2: float | None
    area_sqft: float | None
    ai_status: str | None = None

    def properties(self, *, exported_crs: str | None = None) -> dict[str, object]:
        return {
            "feature_id": self.feature_id,
            "feature_type": self.feature_type,
            "land_use_class": self.land_use_class.value,
            "source": self.source.value,
            "source_reference": self.source_reference,
            "coordinate_space": self.coordinate_space,
            "source_crs": self.source_crs,
            "crs": exported_crs if exported_crs is not None else self.source_crs,
            "status": self.status,
            "verification_status": self.verification_status,
            "confidence": self.confidence,
            "model_version": self.model_version,
            "processed_at": self.processed_at,
            "notes": list(self.notes),
            "warnings": list(self.warnings),
            "area_m2": self.area_m2,
            "area_sqft": self.area_sqft,
            "ai_status": self.ai_status,
        }

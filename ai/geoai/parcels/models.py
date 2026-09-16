"""Stable C.4 parcel acquisition types for later PostGIS and review integration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from shapely.geometry.base import BaseGeometry


class ParcelSource(str, Enum):
    CADASTRAL_GIS = "CADASTRAL_GIS"
    FMB_IMPORT = "FMB_IMPORT"
    GNSS_SURVEY = "GNSS_SURVEY"
    HUMAN_DRAWN = "HUMAN_DRAWN"
    AI_VISIBLE_BOUNDARY = "AI_VISIBLE_BOUNDARY"


class EvidenceType(str, Enum):
    FIELD_BUND = "FIELD_BUND"
    FENCE = "FENCE"
    DITCH = "DITCH"
    ROAD_EDGE = "ROAD_EDGE"
    VEGETATION_EDGE = "VEGETATION_EDGE"
    CROP_TRANSITION = "CROP_TRANSITION"
    WALL = "WALL"
    NO_VISIBLE_EVIDENCE = "NO_VISIBLE_EVIDENCE"


@dataclass(frozen=True)
class ParcelResult:
    parcel_id: str
    source: ParcelSource
    source_reference: str | None
    coordinate_space: str
    source_crs: str | None
    geometry: BaseGeometry | None
    status: str
    verification_status: str
    geometry_version: int
    confidence: float | None
    evidence_type: EvidenceType | None
    model_version: str | None
    processed_at: str
    notes: tuple[str, ...]
    warnings: tuple[str, ...]
    requires_survey: bool
    area_m2: float | None
    area_sqft: float | None
    survey_points: tuple[tuple[float, float], ...] | None = None
    ai_boundary_status: str | None = None

    def properties(self, *, exported_crs: str | None = None) -> dict[str, object]:
        return {
            "parcel_id": self.parcel_id,
            "source": self.source.value,
            "source_reference": self.source_reference,
            "coordinate_space": self.coordinate_space,
            "source_crs": self.source_crs,
            "crs": exported_crs if exported_crs is not None else self.source_crs,
            "status": self.status,
            "verification_status": self.verification_status,
            "geometry_version": self.geometry_version,
            "confidence": self.confidence,
            "evidence_type": self.evidence_type.value if self.evidence_type is not None else None,
            "model_version": self.model_version,
            "processed_at": self.processed_at,
            "notes": list(self.notes),
            "warnings": list(self.warnings),
            "requires_survey": self.requires_survey,
            "area_m2": self.area_m2,
            "area_sqft": self.area_sqft,
            "survey_points": [list(point) for point in self.survey_points] if self.survey_points is not None else None,
            "ai_boundary_status": self.ai_boundary_status,
        }

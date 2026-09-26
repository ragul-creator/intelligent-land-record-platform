"""Pydantic schemas and contracts for GeoPackage GIS interchange."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


# Standard error codes
GIS_IMPORT_FILE_INVALID = "GIS_IMPORT_FILE_INVALID"
GIS_IMPORT_LAYER_NOT_FOUND = "GIS_IMPORT_LAYER_NOT_FOUND"
GIS_IMPORT_CRS_MISSING = "GIS_IMPORT_CRS_MISSING"
GIS_IMPORT_GEOMETRY_INVALID = "GIS_IMPORT_GEOMETRY_INVALID"
GIS_IMPORT_MAPPING_INVALID = "GIS_IMPORT_MAPPING_INVALID"
GIS_EXPORT_FAILED = "GIS_EXPORT_FAILED"


class GeoPackageErrorCode(StrEnum):
    FILE_INVALID = GIS_IMPORT_FILE_INVALID
    LAYER_NOT_FOUND = GIS_IMPORT_LAYER_NOT_FOUND
    CRS_MISSING = GIS_IMPORT_CRS_MISSING
    GEOMETRY_INVALID = GIS_IMPORT_GEOMETRY_INVALID
    MAPPING_INVALID = GIS_IMPORT_MAPPING_INVALID
    EXPORT_FAILED = GIS_EXPORT_FAILED


SUPPORTED_LOGICAL_LAYERS: tuple[str, ...] = ("parcels", "buildings", "roads", "land_use")
LogicalLayer = Literal["parcels", "buildings", "roads", "land_use"]

ImportMode = Literal["DRAFT_IMPORT"]
SUPPORTED_IMPORT_MODES: tuple[str, ...] = ("DRAFT_IMPORT",)

EXPECTED_GEOMETRY_FAMILIES: dict[str, tuple[str, ...]] = {
    "parcels": ("Polygon", "MultiPolygon"),
    "buildings": ("Polygon", "MultiPolygon"),
    "roads": ("LineString", "MultiLineString"),
    "land_use": ("Polygon", "MultiPolygon"),
}

EXPECTED_LAYER_ATTRIBUTES: dict[LogicalLayer, tuple[str, ...]] = {
    "parcels": (
        "id",
        "external_identifier",
        "status",
        "verification_status",
        "source",
        "source_reference",
        "current_geometry_version",
        "area_m2",
        "area_sqft",
        "requires_survey",
        "model_version",
        "confidence",
    ),
    "buildings": (
        "id",
        "status",
        "verification_status",
        "source",
        "source_reference",
        "area_m2",
        "area_sqft",
        "model_version",
        "confidence",
        "processed_at",
    ),
    "roads": (
        "id",
        "status",
        "verification_status",
        "source",
        "source_reference",
        "length_m",
        "model_version",
        "confidence",
        "processed_at",
    ),
    "land_use": (
        "id",
        "status",
        "verification_status",
        "source",
        "source_reference",
        "area_m2",
        "area_sqft",
        "model_version",
        "confidence",
        "processed_at",
    ),
}


class CRSInfo(BaseModel):
    """Structured information about a detected coordinate reference system."""

    detected: bool
    srs_id: int | None = None
    authority: str | None = None
    code: str | int | None = None
    crs_string: str | None = None
    is_geographic: bool | None = None
    is_projected: bool | None = None


class LayerInspection(BaseModel):
    """Summary of a single inspected feature layer inside a GeoPackage."""

    name: str
    geometry_type: str
    feature_count: int
    crs: CRSInfo
    fields: list[str] = Field(default_factory=list)


class GeoPackageInspection(BaseModel):
    """Result of inspecting all feature layers within a GeoPackage file."""

    file_path: str | None = None
    layer_count: int
    layers: list[LayerInspection]


class LayerMappingRequest(BaseModel):
    """User-supplied mapping of logical target layers to GeoPackage source tables."""

    parcels: str | None = Field(default=None, max_length=255)
    buildings: str | None = Field(default=None, max_length=255)
    roads: str | None = Field(default=None, max_length=255)
    land_use: str | None = Field(default=None, max_length=255)
    import_mode: ImportMode = "DRAFT_IMPORT"

    @field_validator("parcels", "buildings", "roads", "land_use", mode="before")
    @classmethod
    def validate_non_empty_layer_name(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Mapped source layer name must be a non-empty string.")
        return value.strip()

    @model_validator(mode="after")
    def validate_mapping_configuration(self) -> "LayerMappingRequest":
        assigned = {
            layer: getattr(self, layer)
            for layer in SUPPORTED_LOGICAL_LAYERS
            if getattr(self, layer) is not None
        }
        if not assigned:
            raise ValueError("At least one logical layer must be mapped.")

        source_to_logicals: dict[str, list[str]] = {}
        for logical, source in assigned.items():
            source_to_logicals.setdefault(source, []).append(logical)

        duplicates = {
            src: targets for src, targets in source_to_logicals.items() if len(targets) > 1
        }
        if duplicates:
            raise ValueError(f"Duplicate source layer assignment detected: {duplicates}")

        return self


class RejectionSummary(BaseModel):
    """Bounded diagnostic metric for rejected geometries without exposing source rows."""

    error_code: str
    reason: str
    count: int


class GeometryValidationResult(BaseModel):
    """Aggregated geometry acceptance, repair, and rejection metrics."""

    accepted_count: int = 0
    rejected_count: int = 0
    repaired_count: int = 0
    warning_codes: list[str] = Field(default_factory=list)
    error_codes: list[str] = Field(default_factory=list)
    rejection_summary: list[RejectionSummary] = Field(default_factory=list)

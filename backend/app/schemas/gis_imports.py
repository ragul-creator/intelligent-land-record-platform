"""Pydantic schemas for GeoPackage GIS import API contracts."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

CANONICAL_LOGICAL_LAYERS = frozenset({"parcels", "buildings", "roads", "land_use"})


class GeoPackageImportCreateRequest(BaseModel):
    """Payload to initiate a GeoPackage GIS import run."""

    file_id: uuid.UUID
    source_reference: str | None = Field(default=None, max_length=1024)
    layer_mapping: dict[str, str] = Field(..., min_length=1)
    mode: Literal["DRAFT_IMPORT"] = "DRAFT_IMPORT"

    @field_validator("layer_mapping")
    @classmethod
    def validate_layer_mapping(cls, mapping: dict[str, str]) -> dict[str, str]:
        if not mapping:
            raise ValueError("Layer mapping cannot be empty.")
        clean_mapping: dict[str, str] = {}
        for key, value in mapping.items():
            k_clean = str(key).strip().lower()
            if k_clean not in CANONICAL_LOGICAL_LAYERS:
                raise ValueError(
                    f"Invalid logical layer '{key}'. Allowed layers: {sorted(CANONICAL_LOGICAL_LAYERS)}"
                )
            v_clean = str(value).strip()
            if not v_clean:
                raise ValueError(f"Target layer name for '{key}' cannot be empty.")
            clean_mapping[k_clean] = v_clean
        return clean_mapping

    @field_validator("source_reference")
    @classmethod
    def validate_source_ref(cls, val: str | None) -> str | None:
        if val is not None:
            cleaned = val.strip()
            return cleaned if cleaned else None
        return None


class GeoPackageImportAcceptedResponse(BaseModel):
    """Accepted response (HTTP 202) indicating the import job is queued."""

    import_run_id: uuid.UUID
    processing_job_id: uuid.UUID
    status: str


class GeoPackageImportSummary(BaseModel):
    """Bounded, validated statistics for an import run."""

    layers_detected: int = 0
    layers_mapped: int = 0
    features_read: int = 0
    features_imported: int = 0
    features_rejected: int = 0
    repairs_applied: int = 0
    warnings: list[str] = Field(default_factory=list)
    rejection_summary: list[dict[str, Any]] = Field(default_factory=list)
    layer_details: dict[str, Any] = Field(default_factory=dict)


class GeoPackageImportDetailResponse(BaseModel):
    """Full detail view for a specific GeoPackage GIS import run."""

    import_run_id: uuid.UUID
    project_id: uuid.UUID
    file_id: uuid.UUID
    processing_job_id: uuid.UUID
    requested_by_user_id: uuid.UUID | None = None
    source_reference: str | None = None
    layer_mapping: dict[str, str]
    status: str
    detected_layers: list[dict[str, Any]] | dict[str, Any] = Field(default_factory=dict)
    summary: GeoPackageImportSummary | dict[str, Any] = Field(default_factory=dict)
    error_code: str | None = None
    created_at: datetime
    updated_at: datetime


class GeoPackageInspectRequest(BaseModel):
    """Request to inspect layers, geometry types, and CRS metadata of an uploaded GeoPackage."""

    file_id: uuid.UUID


class LayerInspectResponse(BaseModel):
    """Inspection metadata for a single feature layer in a GeoPackage."""

    name: str
    geometry_type: str
    feature_count: int
    crs: dict[str, Any]
    fields: list[str] = Field(default_factory=list)


class GeoPackageInspectResponse(BaseModel):
    """Discovered layers and CRS metadata for an uploaded GeoPackage file."""

    file_id: uuid.UUID
    layer_count: int
    layers: list[LayerInspectResponse]


class GeoPackagePreviewRequest(BaseModel):
    """Request to preview GeoPackage validation and mapping without persistence."""

    file_id: uuid.UUID
    layer_mapping: dict[str, str] = Field(..., min_length=1)
    source_reference: str | None = Field(default=None, max_length=1024)

    @field_validator("layer_mapping")
    @classmethod
    def validate_layer_mapping(cls, mapping: dict[str, str]) -> dict[str, str]:
        return GeoPackageImportCreateRequest.validate_layer_mapping(mapping)


class GeoPackagePreviewResponse(BaseModel):
    """Validation preview showing what would be imported before persistent execution."""

    file_id: uuid.UUID
    layers_detected: int
    layers_mapped: int
    summary: GeoPackageImportSummary

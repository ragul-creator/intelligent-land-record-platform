"""Public C.7 contracts for GeoAI jobs and draft parcel geometry history."""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.common import PageMetadata


GeoAIJobType = Literal["PARCEL_IMPORT", "BUILDING_VECTORIZE", "ROAD_IMPORT", "LAND_USE_IMPORT", "TOPOLOGY_VALIDATE"]
GeoAIJobStatus = Literal["QUEUED", "PROCESSING", "COMPLETED", "FAILED", "CANCELLED"]


class GeoAIJobCreateRequest(BaseModel):
    job_type: GeoAIJobType
    source_type: str
    source_payload: dict[str, Any]
    source_crs: str | None = Field(default=None, max_length=255)
    source_reference: str | None = Field(default=None, max_length=1024)
    model_version: str | None = Field(default=None, max_length=255)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=255)


class GeoAIJobResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    job_type: GeoAIJobType
    status: GeoAIJobStatus
    progress: int
    has_error: bool
    output_references: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ParcelVersionResponse(BaseModel):
    id: uuid.UUID
    version: int
    geometry: dict[str, Any] | None
    source: str
    source_reference: str | None
    coordinate_space: str
    source_crs: str | None
    area_m2: float | None
    area_sqft: float | None
    change_reason: str | None
    validation_status: str | None
    created_by_user_id: uuid.UUID | None
    created_by_type: Literal["SYSTEM", "AI", "HUMAN", "IMPORT"]
    processed_at: datetime | None
    created_at: datetime


class ParcelResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    external_identifier: str | None
    source: str
    source_reference: str | None
    status: str
    verification_status: str
    current_geometry_version: int
    coordinate_space: str
    source_crs: str | None
    confidence: float | None
    model_version: str | None
    ai_boundary_status: str | None
    requires_survey: bool
    current_version: ParcelVersionResponse
    created_at: datetime
    updated_at: datetime


class ParcelListResponse(BaseModel):
    items: list[ParcelResponse]
    page: PageMetadata


class ParcelVersionListResponse(BaseModel):
    items: list[ParcelVersionResponse]
    page: PageMetadata


class ParcelVersionCreateRequest(BaseModel):
    geometry: dict[str, Any]
    source_crs: str = Field(min_length=1, max_length=255)
    coordinate_space: Literal["WORLD"] = "WORLD"
    change_reason: str | None = Field(default=None, max_length=2000)


class ParcelVersionCreateResponse(BaseModel):
    version: ParcelVersionResponse
    status: Literal["VALID", "REVIEW_REQUIRED"]
    area_before_m2: float | None
    area_after_m2: float | None
    issues: list[dict[str, Any]]

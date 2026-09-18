"""Contracts for the Phase G.1 record-to-parcel association workflow."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import PageMetadata


LinkStatus = Literal["SUGGESTED", "REVIEW_REQUIRED", "CONFIRMED", "REJECTED"]
LinkMethod = Literal["EXACT_SURVEY_IDENTIFIER", "ATTRIBUTE_MATCH", "SPATIAL_CONTEXT", "MANUAL"]


class LinkSuggestionRequest(BaseModel):
    document_validation_result_id: uuid.UUID


class ManualLinkRequest(LinkSuggestionRequest):
    parcel_id: uuid.UUID
    rationale: str = Field(min_length=1, max_length=2000)

    @field_validator("rationale")
    @classmethod
    def require_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Manual link rationale must not be blank.")
        return value


class LinkResolutionRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


class RecordParcelLinkResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    document_id: uuid.UUID
    document_validation_result_id: uuid.UUID
    parcel_id: uuid.UUID
    parcel_display_identifier: str | None
    link_status: LinkStatus
    link_method: LinkMethod
    confidence: float | None
    rationale: dict[str, Any]
    provenance: dict[str, Any]
    review_required: bool
    review_task_id: uuid.UUID | None
    created_by_user_id: uuid.UUID
    reviewed_by_user_id: uuid.UUID | None
    reviewed_at: datetime | None
    review_reason: str | None
    created_at: datetime
    updated_at: datetime


class RecordParcelLinkListResponse(BaseModel):
    items: list[RecordParcelLinkResponse]
    page: PageMetadata


class RecordParcelLinkSuggestionResponse(BaseModel):
    items: list[RecordParcelLinkResponse]

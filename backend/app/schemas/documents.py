"""Pydantic contracts for the project-scoped Document AI API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import PageMetadata


class DocumentProcessRequest(BaseModel):
    languages: str = Field(default="tam+eng", min_length=1, max_length=255)


class DocumentCorrectionRequest(BaseModel):
    corrected_value: str = Field(min_length=1, max_length=10000)
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("corrected_value", "reason")
    @classmethod
    def no_blank_values(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Value must not be blank.")
        return value


class DocumentSummaryResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    status: str
    latest_processing_job_id: uuid.UUID | None = None
    uploaded_at: datetime
    updated_at: datetime


class DocumentListResponse(BaseModel):
    items: list[DocumentSummaryResponse]
    page: PageMetadata


class SourceUrlResponse(BaseModel):
    document_id: uuid.UUID
    source_url: str
    expires_in_seconds: int


class DocumentJobResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    status: str
    progress: int
    processing_version: int


class OcrResultResponse(BaseModel):
    id: uuid.UUID
    version: int
    status: str
    requested_languages: list[str]
    project_tested_languages: list[str]
    engine: str
    engine_version: str | None
    model_version: str | None
    page_count: int
    confidence: float | None
    payload: dict[str, Any]
    processed_at: datetime


class FieldCorrectionResponse(BaseModel):
    id: uuid.UUID
    version: int
    corrected_value: str
    reason: str
    created_by_user_id: uuid.UUID | None
    created_at: datetime


class ExtractedFieldResponse(BaseModel):
    id: uuid.UUID
    field_name: str
    original_value: str
    normalized_value: Any | None
    confidence: float | None
    page_number: int
    bounding_box: dict[str, int] | None
    source_id: str
    model_version: str | None
    extractor_version: str
    processed_at: datetime
    corrections: list[FieldCorrectionResponse]


class FieldsResponse(BaseModel):
    ocr_result_id: uuid.UUID | None
    fields: list[ExtractedFieldResponse]


class ValidationResultResponse(BaseModel):
    id: uuid.UUID
    version: int
    status: str
    validation_version: str
    report: dict[str, Any]
    confidence_summary: dict[str, Any]
    checks: dict[str, Any]
    review_task_id: uuid.UUID | None
    processed_at: datetime


class DocumentDetailResponse(DocumentSummaryResponse):
    latest_ocr: OcrResultResponse | None
    latest_validation: ValidationResultResponse | None

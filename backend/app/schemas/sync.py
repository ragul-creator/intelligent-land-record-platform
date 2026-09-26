"""Offline-first synchronization API schemas and contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


SyncOperationType = Literal[
    "PARCEL_VERSION_CREATE",
    "FIELD_CORRECTION_CREATE",
    "REVIEW_TASK_UPDATE",
    "RECORD_LINK_RESOLVE",
]

SyncOperationStatus = Literal["APPLIED", "DUPLICATE", "CONFLICT", "REJECTED"]


class SyncOperationItemRequest(BaseModel):
    operation_id: uuid.UUID
    operation_type: str
    entity_id: uuid.UUID
    base_version: int | None = None
    client_created_at: datetime | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class SyncBatchRequest(BaseModel):
    client_id: str | None = None
    operations: list[SyncOperationItemRequest] = Field(default_factory=list)


class SyncConflictDetail(BaseModel):
    conflict_type: str
    expected_version: int | None = None
    server_version: int | None = None
    server_state: str | None = None
    message: str | None = None


class SyncErrorDetail(BaseModel):
    code: str
    message: str


class SyncOperationResult(BaseModel):
    operation_id: uuid.UUID
    status: SyncOperationStatus
    entity_type: str | None = None
    entity_id: uuid.UUID
    server_version: int | None = None
    result: dict[str, Any] | None = None
    conflict: SyncConflictDetail | None = None
    error: SyncErrorDetail | None = None


class SyncBatchResponse(BaseModel):
    project_id: uuid.UUID
    server_cursor: str
    results: list[SyncOperationResult]


class SyncChangeItem(BaseModel):
    cursor: str
    entity_type: str
    entity_id: uuid.UUID
    change_type: str
    server_version: int | None = None
    changed_at: datetime


class SyncChangesResponse(BaseModel):
    items: list[SyncChangeItem]
    next_cursor: str | None = None
    has_more: bool = False


class SyncOperationDetailResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    user_id: uuid.UUID | None = None
    client_id: str | None = None
    operation_type: str
    entity_id: uuid.UUID
    base_version: int | None = None
    status: str
    result: dict[str, Any] | None = None
    conflict: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    client_created_at: datetime | None = None
    applied_at: datetime | None = None
    created_at: datetime

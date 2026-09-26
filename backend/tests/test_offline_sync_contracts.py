"""Unit and contract tests for offline-first sync backend models, schemas, and logic."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.schemas.sync import (
    SyncBatchRequest,
    SyncBatchResponse,
    SyncChangeItem,
    SyncChangesResponse,
    SyncConflictDetail,
    SyncErrorDetail,
    SyncOperationDetailResponse,
    SyncOperationItemRequest,
    SyncOperationResult,
)
from app.models.sync import SyncChange, SyncOperation


def test_sync_operation_item_schema_validation() -> None:
    op_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    item = SyncOperationItemRequest(
        operation_id=op_id,
        operation_type="PARCEL_VERSION_CREATE",
        entity_id=entity_id,
        base_version=3,
        payload={"geometry": {"type": "Polygon", "coordinates": []}, "source_crs": "EPSG:4326"},
    )
    assert item.operation_id == op_id
    assert item.operation_type == "PARCEL_VERSION_CREATE"
    assert item.entity_id == entity_id
    assert item.base_version == 3
    assert item.payload["source_crs"] == "EPSG:4326"


def test_sync_batch_request_schema() -> None:
    batch = SyncBatchRequest(
        client_id="tablet-app-99",
        operations=[
            SyncOperationItemRequest(
                operation_id=uuid.uuid4(),
                operation_type="FIELD_CORRECTION_CREATE",
                entity_id=uuid.uuid4(),
                payload={"corrected_value": "45/2", "reason": "Typo fix"},
            )
        ],
    )
    assert batch.client_id == "tablet-app-99"
    assert len(batch.operations) == 1
    assert batch.operations[0].operation_type == "FIELD_CORRECTION_CREATE"


def test_sync_operation_result_applied_schema() -> None:
    op_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    res = SyncOperationResult(
        operation_id=op_id,
        status="APPLIED",
        entity_type="PARCEL",
        entity_id=entity_id,
        server_version=4,
        result={"parcel_version": 4, "area_m2": 1250.5},
    )
    assert res.status == "APPLIED"
    assert res.server_version == 4
    assert res.conflict is None
    assert res.error is None
    assert res.result["parcel_version"] == 4


def test_sync_operation_result_conflict_schema() -> None:
    op_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    res = SyncOperationResult(
        operation_id=op_id,
        status="CONFLICT",
        entity_type="PARCEL",
        entity_id=entity_id,
        server_version=5,
        conflict=SyncConflictDetail(
            conflict_type="VERSION_MISMATCH",
            expected_version=4,
            server_version=5,
            message="Server has newer version.",
        ),
    )
    assert res.status == "CONFLICT"
    assert res.conflict.conflict_type == "VERSION_MISMATCH"
    assert res.conflict.expected_version == 4
    assert res.conflict.server_version == 5


def test_sync_operation_result_rejected_schema() -> None:
    op_id = uuid.uuid4()
    entity_id = uuid.uuid4()
    res = SyncOperationResult(
        operation_id=op_id,
        status="REJECTED",
        entity_id=entity_id,
        error=SyncErrorDetail(
            code="SYNC_PERMISSION_DENIED",
            message="Missing required permission.",
        ),
    )
    assert res.status == "REJECTED"
    assert res.error.code == "SYNC_PERMISSION_DENIED"


def test_sync_batch_response_serialization() -> None:
    proj_id = uuid.uuid4()
    op_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    response = SyncBatchResponse(
        project_id=proj_id,
        server_cursor="1042",
        results=[
            SyncOperationResult(
                operation_id=op_id,
                status="APPLIED",
                entity_type="DOCUMENT_FIELD",
                entity_id=entity_id,
                server_version=2,
                result={"correction_id": str(uuid.uuid4())},
            )
        ],
    )
    data = response.model_dump()
    assert data["project_id"] == proj_id
    assert data["server_cursor"] == "1042"
    assert len(data["results"]) == 1
    assert data["results"][0]["status"] == "APPLIED"


def test_sync_changes_response_schema() -> None:
    change_id = uuid.uuid4()
    res = SyncChangesResponse(
        items=[
            SyncChangeItem(
                cursor="101",
                entity_type="PARCEL",
                entity_id=change_id,
                change_type="UPDATED",
                server_version=2,
                changed_at=datetime.now(UTC),
            )
        ],
        next_cursor="101",
        has_more=False,
    )
    assert len(res.items) == 1
    assert res.items[0].cursor == "101"
    assert res.next_cursor == "101"
    assert res.has_more is False


def test_sync_operation_model_instantiation() -> None:
    op_id = uuid.uuid4()
    proj_id = uuid.uuid4()
    user_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    op = SyncOperation(
        id=op_id,
        project_id=proj_id,
        user_id=user_id,
        client_id="client-xyz",
        operation_type="PARCEL_VERSION_CREATE",
        entity_id=entity_id,
        base_version=1,
        payload_json={"geometry": {}},
        status="APPLIED",
        result_json={"parcel_version": 2},
    )
    assert op.id == op_id
    assert op.project_id == proj_id
    assert op.status == "APPLIED"
    assert op.operation_type == "PARCEL_VERSION_CREATE"


def test_sync_change_model_instantiation() -> None:
    proj_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    change = SyncChange(
        project_id=proj_id,
        entity_type="REVIEW_TASK",
        entity_id=entity_id,
        change_type="RESOLVED",
        server_version=None,
    )
    assert change.project_id == proj_id
    assert change.entity_type == "REVIEW_TASK"
    assert change.change_type == "RESOLVED"

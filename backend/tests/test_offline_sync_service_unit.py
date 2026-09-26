"""Unit tests for offline-first sync service logic and batch processing."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from pydantic import ValidationError

from app.core.errors import ApiError
from app.models import Parcel, SyncChange, SyncOperation, User
from app.schemas.sync import (
    SyncBatchRequest,
    SyncOperationItemRequest,
)
from app.services.sync import (
    MAX_SYNC_BATCH_SIZE,
    apply_sync_batch,
    get_sync_changes,
    get_sync_operation_by_id,
)


def test_batch_size_limit_exceeded_raises_api_error() -> None:
    session = MagicMock()
    user = MagicMock()
    project_id = uuid.uuid4()

    large_batch = SyncBatchRequest(
        operations=[
            SyncOperationItemRequest(
                operation_id=uuid.uuid4(),
                operation_type="PARCEL_VERSION_CREATE",
                entity_id=uuid.uuid4(),
                base_version=1,
                payload={},
            )
            for _ in range(MAX_SYNC_BATCH_SIZE + 1)
        ]
    )

    with pytest.raises(ApiError) as exc_info:
        apply_sync_batch(session, project_id=project_id, user=user, batch=large_batch)

    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "SYNC_BATCH_TOO_LARGE"


def test_unsupported_operation_returns_rejected() -> None:
    session = MagicMock()
    user = MagicMock(id=uuid.uuid4())
    project_id = uuid.uuid4()
    op_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    # Mock no existing operation
    session.get.return_value = None
    session.scalar.return_value = 0

    with patch("app.services.sync.user_permissions", return_value={"geo:edit_draft"}):
        batch = SyncBatchRequest(
            operations=[
                SyncOperationItemRequest(
                    operation_id=op_id,
                    operation_type="DOCUMENT_UPLOAD",  # Unsupported
                    entity_id=entity_id,
                    payload={},
                )
            ]
        )
        res = apply_sync_batch(session, project_id=project_id, user=user, batch=batch)

    assert len(res.results) == 1
    assert res.results[0].status == "REJECTED"
    assert res.results[0].error.code == "SYNC_OPERATION_UNSUPPORTED"


def test_missing_required_permission_returns_rejected() -> None:
    session = MagicMock()
    user = MagicMock(id=uuid.uuid4())
    project_id = uuid.uuid4()
    op_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    session.get.return_value = None
    session.scalar.return_value = 0

    # User only has geo:read, not geo:edit_draft
    with patch("app.services.sync.user_permissions", return_value={"geo:read"}):
        batch = SyncBatchRequest(
            operations=[
                SyncOperationItemRequest(
                    operation_id=op_id,
                    operation_type="PARCEL_VERSION_CREATE",
                    entity_id=entity_id,
                    base_version=1,
                    payload={"geometry": {}, "source_crs": "EPSG:4326"},
                )
            ]
        )
        res = apply_sync_batch(session, project_id=project_id, user=user, batch=batch)

    assert len(res.results) == 1
    assert res.results[0].status == "REJECTED"
    assert res.results[0].error.code == "SYNC_PERMISSION_DENIED"


def test_duplicate_operation_id_returns_duplicate_status() -> None:
    session = MagicMock()
    user = MagicMock(id=uuid.uuid4())
    project_id = uuid.uuid4()
    op_id = uuid.uuid4()
    entity_id = uuid.uuid4()

    existing_op = SyncOperation(
        id=op_id,
        project_id=project_id,
        user_id=user.id,
        client_id="test-client",
        operation_type="PARCEL_VERSION_CREATE",
        entity_id=entity_id,
        base_version=1,
        status="APPLIED",
        result_json={"parcel_version": 2, "status": "VALID"},
    )
    session.get.return_value = existing_op
    session.scalar.return_value = 5

    with patch("app.services.sync.user_permissions", return_value={"geo:edit_draft"}):
        batch = SyncBatchRequest(
            operations=[
                SyncOperationItemRequest(
                    operation_id=op_id,
                    operation_type="PARCEL_VERSION_CREATE",
                    entity_id=entity_id,
                    base_version=1,
                    payload={"geometry": {}, "source_crs": "EPSG:4326"},
                )
            ]
        )
        res = apply_sync_batch(session, project_id=project_id, user=user, batch=batch)

    assert len(res.results) == 1
    assert res.results[0].status == "DUPLICATE"
    assert res.results[0].server_version == 2
    assert res.results[0].result["parcel_version"] == 2


def test_change_feed_pagination_logic() -> None:
    session = MagicMock()
    project_id = uuid.uuid4()

    fake_changes = [
        SyncChange(
            id=1,
            project_id=project_id,
            entity_type="PARCEL",
            entity_id=uuid.uuid4(),
            change_type="UPDATED",
            server_version=2,
            changed_at=datetime.now(UTC),
        ),
        SyncChange(
            id=2,
            project_id=project_id,
            entity_type="DOCUMENT_FIELD",
            entity_id=uuid.uuid4(),
            change_type="UPDATED",
            server_version=1,
            changed_at=datetime.now(UTC),
        ),
        SyncChange(
            id=3,
            project_id=project_id,
            entity_type="REVIEW_TASK",
            entity_id=uuid.uuid4(),
            change_type="RESOLVED",
            server_version=None,
            changed_at=datetime.now(UTC),
        ),
    ]

    # Return 3 changes when limit=2 (has_more=True)
    session.scalars.return_value = fake_changes
    res = get_sync_changes(session, project_id=project_id, cursor="0", limit=2)
    assert len(res.items) == 2
    assert res.has_more is True
    assert res.next_cursor == "2"

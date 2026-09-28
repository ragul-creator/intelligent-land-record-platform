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
    def _mock_scalar(statement):
        sql = str(statement)
        if "sync_operations" in sql or "SyncOperation" in sql:
            return existing_op
        return 5

    session.get.return_value = existing_op
    session.scalar.side_effect = _mock_scalar

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


def test_operation_id_project_scoped_isolation_never_leaks_or_replays() -> None:
    """Project A + Operation X vs Project B + Operation X must never leak or replay Project A to B."""
    session = MagicMock()
    user = MagicMock(id=uuid.uuid4())
    project_a = uuid.uuid4()
    project_b = uuid.uuid4()
    op_x = uuid.uuid4()
    entity_a = uuid.uuid4()

    existing_op_a = SyncOperation(
        id=op_x,
        project_id=project_a,
        user_id=user.id,
        client_id="client-a",
        operation_type="PARCEL_VERSION_CREATE",
        entity_id=entity_a,
        base_version=1,
        status="APPLIED",
        result_json={"parcel_version": 2, "secret_notes": "Project A confidential"},
    )

    # Project-scoped get: only returns existing_op_a if queried for project_a
    def _mock_get(model, pk):
        if model is SyncOperation:
            if isinstance(pk, tuple) and pk == (op_x, project_a):
                return existing_op_a
            return None
        if model is Parcel:
            return parcel_b
        return None

    session.get.side_effect = _mock_get

    # Mock parcel in project B
    parcel_b = Parcel(
        id=uuid.uuid4(),
        project_id=project_b,
        current_geometry_version=1,
        coordinate_space="WORLD",
        source="TEST",
    )

    def _scoped_scalar(statement):
        sql = str(statement)
        if "sync_operations" in sql or "SyncOperation" in sql:
            return None
        if "parcels" in sql or "Parcel" in sql:
            return parcel_b
        return 0

    session.scalar.side_effect = _scoped_scalar

    with patch("app.services.sync.user_permissions", return_value={"geo:edit_draft"}), \
         patch("app.services.sync.create_human_parcel_version") as mock_create_version:
        mock_version = MagicMock(version=2, area_m2=100.0, area_sqft=1076.39)
        mock_result = MagicMock(status="VALID")
        mock_create_version.return_value = (mock_version, mock_result)

        batch = SyncBatchRequest(
            operations=[
                SyncOperationItemRequest(
                    operation_id=op_x,
                    operation_type="PARCEL_VERSION_CREATE",
                    entity_id=parcel_b.id,
                    base_version=1,
                    payload={"geometry": {}, "source_crs": "EPSG:4326"},
                )
            ]
        )
        res = apply_sync_batch(session, project_id=project_b, user=user, batch=batch)

    assert len(res.results) == 1
    # Must NOT be DUPLICATE and must NOT have Project A's entity or secret notes!
    assert res.results[0].status == "APPLIED"
    assert res.results[0].entity_id == parcel_b.id
    assert res.results[0].entity_id != entity_a
    if res.results[0].result:
        assert "Project A confidential" not in str(res.results[0].result)


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


def test_malformed_cursor_rejected_with_bad_request() -> None:
    """F-16: Verify malformed cursor values are rejected with 400 INVALID_CURSOR."""
    session = MagicMock()
    project_id = uuid.uuid4()

    malformed_cursors = ["invalid", "-1", "-999", "1; DROP TABLE sync_changes;", "abc", "null", "NaN"]
    for bad_cursor in malformed_cursors:
        with pytest.raises(ApiError) as exc_info:
            get_sync_changes(session, project_id=project_id, cursor=bad_cursor)
        assert exc_info.value.status_code == 400
        assert exc_info.value.code == "INVALID_CURSOR"


def test_field_correction_retry_preserves_idempotency() -> None:
    """F-18: Verify field-correction retry does not duplicate mutations and preserves idempotency."""
    session = MagicMock()
    user = MagicMock(id=uuid.uuid4())
    project_id = uuid.uuid4()
    op_id = uuid.uuid4()
    field_id = uuid.uuid4()

    # Existing completed SyncOperation in DB (simulating retry after network drop)
    existing_op = SyncOperation(
        id=op_id,
        project_id=project_id,
        user_id=user.id,
        client_id="mobile-client-1",
        operation_type="FIELD_CORRECTION_CREATE",
        entity_id=field_id,
        base_version=None,
        status="APPLIED",
        result_json={
            "correction_id": str(uuid.uuid4()),
            "version": 2,
            "corrected_value": "Corrected Survey No",
        },
    )

    # session returns existing_op when looking up by (op_id, project_id)
    session.get.return_value = existing_op
    session.scalar.return_value = existing_op

    batch = SyncBatchRequest(
        operations=[
            SyncOperationItemRequest(
                operation_id=op_id,
                operation_type="FIELD_CORRECTION_CREATE",
                entity_id=field_id,
                payload={"corrected_value": "Corrected Survey No", "reason": "Typo fix"},
            )
        ]
    )

    res = apply_sync_batch(session, project_id=project_id, user=user, batch=batch)
    assert len(res.results) == 1
    result = res.results[0]
    assert result.status == "DUPLICATE"
    assert result.operation_id == op_id
    assert result.entity_id == field_id
    assert result.result["version"] == 2
    assert result.result["corrected_value"] == "Corrected Survey No"
    # Ensure no domain mutation was attempted
    session.add.assert_not_called()


def test_client_id_exceeding_max_length_rejected() -> None:
    """F-17: Verify client_id > 128 characters is rejected cleanly."""
    session = MagicMock()
    user = MagicMock(id=uuid.uuid4())
    project_id = uuid.uuid4()

    # Via Pydantic schema validation
    with pytest.raises(ValidationError):
        SyncBatchRequest(client_id="a" * 129, operations=[])

    # Via direct service call
    batch = SyncBatchRequest.model_construct(client_id="a" * 129, operations=[])
    with pytest.raises(ApiError) as exc_info:
        apply_sync_batch(session, project_id=project_id, user=user, batch=batch)
    assert exc_info.value.status_code == 422
    assert exc_info.value.code == "SYNC_CLIENT_ID_TOO_LONG"


def test_oversized_payload_rejected_cleanly() -> None:
    """F-17: Verify payload > 256KB is safely rejected with SYNC_OPERATION_INVALID."""
    session = MagicMock()
    user = MagicMock(id=uuid.uuid4())
    project_id = uuid.uuid4()
    op_id = uuid.uuid4()
    session.get.return_value = None
    session.scalar.return_value = 0

    large_payload = {"huge_blob": "x" * (300 * 1024)}  # 300 KB
    batch = SyncBatchRequest(
        operations=[
            SyncOperationItemRequest(
                operation_id=op_id,
                operation_type="FIELD_CORRECTION_CREATE",
                entity_id=uuid.uuid4(),
                payload=large_payload,
            )
        ]
    )

    with patch("app.services.sync.user_permissions", return_value={"field:correct"}):
        res = apply_sync_batch(session, project_id=project_id, user=user, batch=batch)

    assert len(res.results) == 1
    assert res.results[0].status == "REJECTED"
    assert res.results[0].error.code == "SYNC_OPERATION_INVALID"
    assert "exceeds maximum limit" in res.results[0].error.message


def test_oversized_geometry_coordinates_rejected() -> None:
    """F-17: Verify geometry with >5000 coordinates is rejected before GIS processing."""
    session = MagicMock()
    user = MagicMock(id=uuid.uuid4())
    project_id = uuid.uuid4()
    op_id = uuid.uuid4()
    parcel_id = uuid.uuid4()

    # Generate a polygon with 5001 coordinates
    excessive_ring = [[float(i), float(i)] for i in range(5001)]
    excessive_geom = {"type": "Polygon", "coordinates": [excessive_ring]}

    session.get.return_value = None
    session.scalar.return_value = 0

    batch = SyncBatchRequest(
        operations=[
            SyncOperationItemRequest(
                operation_id=op_id,
                operation_type="PARCEL_VERSION_CREATE",
                entity_id=parcel_id,
                base_version=1,
                payload={"geometry": excessive_geom, "source_crs": "EPSG:4326"},
            )
        ]
    )

    with patch("app.services.sync.user_permissions", return_value={"geo:edit_draft"}):
        res = apply_sync_batch(session, project_id=project_id, user=user, batch=batch)

    assert len(res.results) == 1
    assert res.results[0].status == "REJECTED"
    assert res.results[0].error.code == "SYNC_OPERATION_INVALID"
    assert "coordinate count" in res.results[0].error.message

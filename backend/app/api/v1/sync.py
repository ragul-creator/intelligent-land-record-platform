"""Offline-first synchronization and change feed API endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db_session
from app.models import User
from app.schemas.sync import (
    SyncBatchRequest,
    SyncBatchResponse,
    SyncChangesResponse,
    SyncOperationDetailResponse,
)
from app.services.project_access import get_project_for_user
from app.services.sync import (
    apply_sync_batch,
    get_sync_changes,
    get_sync_operation_by_id,
)

router = APIRouter(prefix="/projects/{project_id}/sync", tags=["sync"])


@router.post("/batch", response_model=SyncBatchResponse, status_code=status.HTTP_200_OK)
def sync_batch(
    project_id: uuid.UUID,
    request: SyncBatchRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> SyncBatchResponse:
    """Submit a batch of offline-created mutations with isolated savepoints and idempotency."""
    get_project_for_user(session, user, project_id, "project:read")
    response = apply_sync_batch(session, project_id=project_id, user=user, batch=request)
    session.commit()
    return response


@router.get("/changes", response_model=SyncChangesResponse)
def list_sync_changes(
    project_id: uuid.UUID,
    cursor: str | None = Query(default=None, description="Monotonic sequence cursor"),
    limit: int = Query(default=50, ge=1, le=100),
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> SyncChangesResponse:
    """Bounded, project-scoped change feed for offline client catching-up."""
    get_project_for_user(session, user, project_id, "project:read")
    return get_sync_changes(session, project_id=project_id, cursor=cursor, limit=limit)


@router.get("/operations/{operation_id}", response_model=SyncOperationDetailResponse)
def get_sync_operation(
    project_id: uuid.UUID,
    operation_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> SyncOperationDetailResponse:
    """Retrieve persisted status of a single offline operation for client recovery."""
    get_project_for_user(session, user, project_id, "project:read")
    return get_sync_operation_by_id(session, project_id=project_id, operation_id=operation_id)

"""Phase E.2 project-scoped review queue and reviewer actions."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db_session
from app.core.errors import ApiError, not_found
from app.models import AuditLog, ReviewTask, User
from app.schemas.common import PageMetadata
from app.schemas.review import (
    ReviewHistoryEvent,
    ReviewTaskDetailResponse,
    ReviewTaskListResponse,
    ReviewTaskResponse,
    ReviewTaskUpdateRequest,
)
from app.services.project_access import get_project_for_user
from app.services.review import ReviewWorkflowError, update_review_task


router = APIRouter(prefix="/review/tasks", tags=["review"])


def _response(task: ReviewTask) -> ReviewTaskResponse:
    return ReviewTaskResponse(
        id=task.id,
        project_id=task.project_id,
        queue_type=task.queue_type,
        target_type=task.target_type,
        target_id=task.target_id,
        severity=task.severity,
        status=task.status,
        summary=task.summary,
        source_refs=list(task.source_refs_json or []),
        metadata=dict(task.metadata_json or {}),
        blocking_issue_count=task.blocking_issue_count,
        assignee_user_id=task.assignee_user_id,
        created_by_user_id=task.created_by_user_id,
        escalated=task.escalated,
        resolution_action=task.resolution_action,
        resolved_at=task.resolved_at,
        resolved_by_user_id=task.resolved_by_user_id,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def _task_for_user(
    session: Session,
    user: User,
    task_id: uuid.UUID,
    permission: str,
) -> ReviewTask:
    task = session.get(ReviewTask, task_id)
    if task is None:
        raise not_found("REVIEW_TASK_NOT_FOUND", "The requested review task was not found.")
    get_project_for_user(session, user, task.project_id, permission)
    return task


def _history(session: Session, task_id: uuid.UUID) -> list[ReviewHistoryEvent]:
    events = list(
        session.scalars(
            select(AuditLog)
            .where(AuditLog.target_type == "review_task", AuditLog.target_id == task_id)
            .order_by(AuditLog.created_at, AuditLog.id)
        )
    )
    return [
        ReviewHistoryEvent(
            action=event.action,
            actor_id=event.actor_id,
            metadata=event.metadata_json or {},
            created_at=event.created_at,
        )
        for event in events
    ]


@router.get("", response_model=ReviewTaskListResponse)
def list_review_tasks(
    project_id: uuid.UUID,
    queue_type: str | None = Query(default=None, pattern="^(DOCUMENT|GIS)$"),
    task_status: str | None = Query(default=None, alias="status", pattern="^(OPEN|RESOLVED)$"),
    severity: str | None = Query(default=None, pattern="^(INFO|LOW|MEDIUM|HIGH)$"),
    assignee_user_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ReviewTaskListResponse:
    get_project_for_user(session, user, project_id, "review:read")
    filters = [ReviewTask.project_id == project_id]
    if queue_type:
        filters.append(ReviewTask.queue_type == queue_type)
    if task_status:
        filters.append(ReviewTask.status == task_status)
    if severity:
        filters.append(ReviewTask.severity == severity)
    if assignee_user_id:
        filters.append(ReviewTask.assignee_user_id == assignee_user_id)

    total = session.scalar(select(func.count(ReviewTask.id)).where(*filters)) or 0
    tasks = list(
        session.scalars(
            select(ReviewTask)
            .where(*filters)
            .order_by(ReviewTask.created_at.desc(), ReviewTask.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return ReviewTaskListResponse(
        items=[_response(task) for task in tasks],
        page=PageMetadata(limit=limit, offset=offset, total=total),
    )


@router.get("/{task_id}", response_model=ReviewTaskDetailResponse)
def get_review_task(
    task_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ReviewTaskDetailResponse:
    task = _task_for_user(session, user, task_id, "review:read")
    return ReviewTaskDetailResponse(**_response(task).model_dump(), history=_history(session, task.id))


@router.patch("/{task_id}", response_model=ReviewTaskDetailResponse)
def update_review_task_api(
    task_id: uuid.UUID,
    request: ReviewTaskUpdateRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ReviewTaskDetailResponse:
    task = _task_for_user(session, user, task_id, "review:act")
    try:
        update_review_task(
            session,
            task,
            actor=user,
            action=request.action,
            assignee_user_id=request.assignee_user_id,
            reason=request.reason,
            correction_reference=request.correction_reference,
            reprocess_job_id=request.reprocess_job_id,
        )
    except ReviewWorkflowError as error:
        raise ApiError(status.HTTP_409_CONFLICT, "REVIEW_ACTION_INVALID", str(error)) from error
    session.commit()
    session.refresh(task)
    return ReviewTaskDetailResponse(**_response(task).model_dump(), history=_history(session, task.id))

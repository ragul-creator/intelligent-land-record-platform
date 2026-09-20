"""H.2B.3 project-scoped validation checks and issue listing."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.database import get_db_session
from app.models import ReviewTask, User
from app.schemas.common import PageMetadata
from app.schemas.review import ReviewTaskListResponse, ReviewTaskResponse
from app.schemas.validation import ValidationRunResponse
from app.services.project_access import get_project_for_user
from app.services.validation import (
    VALIDATION_ISSUE_TARGET_TYPE,
    VALIDATION_ISSUE_TYPES,
    run_validation_checks,
)


router = APIRouter(prefix="/projects/{project_id}/validation", tags=["validation"])


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


def _issue_filters(
    project_id: uuid.UUID,
    *,
    task_status: str | None = None,
    severity: str | None = None,
    issue_type: str | None = None,
    assignee_user_id: uuid.UUID | None = None,
):
    filters = [
        ReviewTask.project_id == project_id,
        ReviewTask.target_type == VALIDATION_ISSUE_TARGET_TYPE,
    ]
    if task_status:
        filters.append(ReviewTask.status == task_status)
    if severity:
        filters.append(ReviewTask.severity == severity)
    if issue_type:
        filters.append(ReviewTask.metadata_json["validation_issue_type"].astext == issue_type)
    if assignee_user_id:
        filters.append(ReviewTask.assignee_user_id == assignee_user_id)
    return filters


@router.get("/issues", response_model=ReviewTaskListResponse)
def list_validation_issues(
    project_id: uuid.UUID,
    task_status: str | None = Query(default=None, alias="status", pattern="^(OPEN|RESOLVED)$"),
    severity: str | None = Query(default=None, pattern="^(INFO|LOW|MEDIUM|HIGH)$"),
    issue_type: str | None = Query(default=None, pattern="^(DUPLICATE_RECORD|AREA_MISMATCH)$"),
    assignee_user_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ReviewTaskListResponse:
    get_project_for_user(session, user, project_id, "review:read")
    filters = _issue_filters(
        project_id,
        task_status=task_status,
        severity=severity,
        issue_type=issue_type,
        assignee_user_id=assignee_user_id,
    )
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


@router.post("/run", response_model=ValidationRunResponse, status_code=status.HTTP_201_CREATED)
def run_project_validation(
    project_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ValidationRunResponse:
    get_project_for_user(session, user, project_id, "validation:run")
    result = run_validation_checks(session, project_id=project_id, actor=user)
    session.commit()

    open_filters = _issue_filters(project_id, task_status="OPEN")
    open_tasks = list(
        session.scalars(
            select(ReviewTask)
            .where(*open_filters)
            .order_by(ReviewTask.created_at.desc(), ReviewTask.id)
        )
    )
    duplicate_count = sum(
        1 for task in open_tasks
        if (task.metadata_json or {}).get("validation_issue_type") == "DUPLICATE_RECORD"
    )
    mismatch_count = sum(
        1 for task in open_tasks
        if (task.metadata_json or {}).get("validation_issue_type") == "AREA_MISMATCH"
    )
    return ValidationRunResponse(
        created_count=result.created_count,
        refreshed_count=result.refreshed_count,
        open_issue_count=len(open_tasks),
        duplicate_record_issue_count=duplicate_count,
        area_mismatch_issue_count=mismatch_count,
        items=[_response(task) for task in open_tasks],
    )

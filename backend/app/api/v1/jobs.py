"""Project-scoped read APIs for persisted asynchronous processing jobs."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.auth import get_current_user, user_permissions
from app.core.errors import forbidden, not_found
from app.core.database import get_db_session
from app.models import ProcessingJob, ProjectMember, User
from app.schemas.common import PageMetadata
from app.schemas.jobs import ProcessingJobListResponse, ProcessingJobResponse, ProcessingJobStatus
from app.services.project_access import get_project_for_user

project_router = APIRouter(prefix="/projects", tags=["processing jobs"])
router = APIRouter(prefix="/processing-jobs", tags=["processing jobs"])


def _job_response(job: ProcessingJob) -> ProcessingJobResponse:
    return ProcessingJobResponse(
        id=job.id,
        project_id=job.project_id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        retry_count=job.retry_count,
        has_error=job.error_json is not None,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@project_router.get("/{project_id}/jobs", response_model=ProcessingJobListResponse)
def list_project_jobs(
    project_id: uuid.UUID,
    status: ProcessingJobStatus | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ProcessingJobListResponse:
    project = get_project_for_user(session, user, project_id, "project:read")
    filters = [ProcessingJob.project_id == project.id]
    if status is not None:
        filters.append(ProcessingJob.status == status)
    total = session.scalar(select(func.count(ProcessingJob.id)).where(*filters)) or 0
    jobs = list(
        session.scalars(
            select(ProcessingJob)
            .where(*filters)
            .order_by(ProcessingJob.created_at.desc(), ProcessingJob.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return ProcessingJobListResponse(
        items=[_job_response(job) for job in jobs],
        page=PageMetadata(limit=limit, offset=offset, total=total),
    )


@router.get("/{job_id}", response_model=ProcessingJobResponse)
def get_processing_job(
    job_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ProcessingJobResponse:
    if "project:read" not in user_permissions(session, user.id):
        raise forbidden("PROCESSING_JOB_FORBIDDEN", "You are not allowed to access this processing job.")
    job = session.scalar(
        select(ProcessingJob)
        .join(ProjectMember, ProjectMember.project_id == ProcessingJob.project_id)
        .where(ProcessingJob.id == job_id, ProjectMember.user_id == user.id)
    )
    if job is None:
        raise not_found("PROCESSING_JOB_NOT_FOUND", "The requested processing job was not found.")
    return _job_response(job)

"""Project-scoped read and recovery APIs for persisted asynchronous processing jobs."""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.core.auth import get_current_user, user_permissions
from app.core.database import get_db_session
from app.core.errors import ApiError, forbidden, not_found
from app.models import (
    Document,
    DocumentProcessingJob,
    GeoAIJob,
    ImageryAsset,
    ProcessingJob,
    ProjectMember,
    User,
)
from app.schemas.common import PageMetadata
from app.schemas.jobs import ProcessingJobListResponse, ProcessingJobResponse, ProcessingJobStatus
from app.services.processing_jobs import InvalidJobTransition, requeue_failed_job
from app.services.project_access import get_project_for_user

project_router = APIRouter(prefix="/projects", tags=["processing jobs"])
router = APIRouter(prefix="/processing-jobs", tags=["processing jobs"])

RETRYABLE_JOB_TYPES = frozenset(
    {
        "DOCUMENT_AI_PROCESS",
        "DOCUMENT_REVALIDATE",
        "PARCEL_IMPORT",
        "BUILDING_VECTORIZE",
        "IMAGERY_REGISTER",
    }
)


def _job_response(job: ProcessingJob) -> ProcessingJobResponse:
    retryable = job.status == "FAILED" and job.job_type in RETRYABLE_JOB_TYPES
    hints = {
        "DOCUMENT_AI_PROCESS": "Retry the document processing workflow from its immutable source.",
        "DOCUMENT_REVALIDATE": "Retry validation against the persisted OCR and correction evidence.",
        "PARCEL_IMPORT": "Retry the parcel import with the original persisted request parameters.",
        "BUILDING_VECTORIZE": "Retry building inference against the same registered imagery.",
        "IMAGERY_REGISTER": "Retry GeoTIFF inspection and private preview generation.",
    }
    return ProcessingJobResponse(
        id=job.id,
        project_id=job.project_id,
        job_type=job.job_type,
        status=job.status,
        progress=job.progress,
        retry_count=job.retry_count,
        has_error=job.error_json is not None,
        retryable=retryable,
        recovery_hint=hints.get(job.job_type) if retryable else None,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


def _visible_job(session: Session, user: User, job_id: uuid.UUID) -> ProcessingJob:
    if "project:read" not in user_permissions(session, user.id):
        raise forbidden("PROCESSING_JOB_FORBIDDEN", "You are not allowed to access this processing job.")
    job = session.scalar(
        select(ProcessingJob)
        .join(ProjectMember, ProjectMember.project_id == ProcessingJob.project_id)
        .where(ProcessingJob.id == job_id, ProjectMember.user_id == user.id)
    )
    if job is None:
        raise not_found("PROCESSING_JOB_NOT_FOUND", "The requested processing job was not found.")
    return job


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
    return _job_response(_visible_job(session, user, job_id))


@router.post("/{job_id}/retry", response_model=ProcessingJobResponse, status_code=status.HTTP_202_ACCEPTED)
def retry_processing_job(
    job_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ProcessingJobResponse:
    job = _visible_job(session, user, job_id)
    if job.job_type not in RETRYABLE_JOB_TYPES:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "PROCESSING_JOB_NOT_RETRYABLE",
            "This job type does not support safe manual recovery.",
        )

    dispatch = None
    dispatch_args: tuple[str, ...] = ()
    dispatch_queue: str | None = None

    document_job = session.get(DocumentProcessingJob, job.id)
    geoai_job = session.get(GeoAIJob, job.id)

    if document_job is not None:
        get_project_for_user(session, user, job.project_id, "document:reprocess")
        document = session.get(Document, document_job.document_id)
        if document is None or document.project_id != job.project_id:
            raise not_found("DOCUMENT_NOT_FOUND", "The document for this processing job was not found.")
        from app.workers.tasks import process_document_ai, revalidate_document

        if document_job.job_type == "DOCUMENT_REVALIDATE":
            document.status = "VALIDATING"
            dispatch = revalidate_document
        else:
            document.status = "QUEUED"
            dispatch = process_document_ai
        dispatch_args = (str(job.id),)
    elif geoai_job is not None:
        get_project_for_user(session, user, job.project_id, "geoai:process")
        from app.workers.tasks import process_geoai_buildings, process_geoai_parcel_import

        if geoai_job.job_type == "PARCEL_IMPORT":
            dispatch = process_geoai_parcel_import
        elif geoai_job.job_type == "BUILDING_VECTORIZE":
            dispatch = process_geoai_buildings
            dispatch_queue = "geoai"
        else:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "PROCESSING_JOB_NOT_RETRYABLE",
                "This GeoAI workflow does not support safe manual recovery.",
            )
        dispatch_args = (str(job.id),)
    elif job.job_type == "IMAGERY_REGISTER":
        get_project_for_user(session, user, job.project_id, "imagery:upload")
        asset = session.scalar(
            select(ImageryAsset).where(
                ImageryAsset.project_id == job.project_id,
                ImageryAsset.metadata_json["registration_job_id"].astext == str(job.id),
            )
        )
        if asset is None:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "PROCESSING_JOB_RECOVERY_CONTEXT_MISSING",
                "The imagery registration context is no longer available.",
            )
        metadata = dict(asset.metadata_json or {})
        metadata["registration_status"] = "QUEUED"
        asset.metadata_json = metadata
        from app.workers.tasks import process_imagery_registration

        dispatch = process_imagery_registration
        dispatch_args = (str(job.id), str(asset.id))
        dispatch_queue = "geoai"
    else:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "PROCESSING_JOB_RECOVERY_CONTEXT_MISSING",
            "The processing job recovery context is unavailable.",
        )

    try:
        requeue_failed_job(job)
    except InvalidJobTransition as error:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "PROCESSING_JOB_NOT_RETRYABLE",
            "Only failed jobs can be retried.",
        ) from error

    record_audit(
        session,
        "processing_job.manual_retry_queued",
        "processing_job",
        job.id,
        actor_id=user.id,
        project_id=job.project_id,
        metadata={"job_type": job.job_type, "retry_count": job.retry_count},
    )
    session.commit()

    if dispatch_queue is not None:
        dispatch.apply_async(args=list(dispatch_args), queue=dispatch_queue)
    else:
        dispatch.delay(*dispatch_args)

    return _job_response(job)

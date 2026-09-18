"""Small persistence service for idempotent asynchronous processing jobs."""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import ProcessingJob

JOB_STATUSES = frozenset({"QUEUED", "PROCESSING", "COMPLETED", "FAILED", "CANCELLED"})


class InvalidJobTransition(ValueError):
    """Raised when a worker attempts an unsafe terminal-state transition."""


def create_or_get_job(
    session: Session,
    project_id: uuid.UUID,
    job_type: str,
    idempotency_key: str,
) -> tuple[ProcessingJob, bool]:
    """Return the existing project-scoped job when an idempotency key repeats."""
    existing = session.scalar(
        select(ProcessingJob).where(
            ProcessingJob.project_id == project_id,
            ProcessingJob.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing, False

    job = ProcessingJob(
        project_id=project_id,
        job_type=job_type,
        idempotency_key=idempotency_key,
        status="QUEUED",
    )
    session.add(job)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(
            select(ProcessingJob).where(
                ProcessingJob.project_id == project_id,
                ProcessingJob.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            raise
        return existing, False
    return job, True


def mark_job_processing(session: Session, job: ProcessingJob) -> None:
    if job.status != "QUEUED":
        raise InvalidJobTransition(f"Cannot process a {job.status} job.")
    job.status = "PROCESSING"
    job.progress = 1


def mark_job_completed(session: Session, job: ProcessingJob) -> None:
    if job.status != "PROCESSING":
        raise InvalidJobTransition(f"Cannot complete a {job.status} job.")
    job.status = "COMPLETED"
    job.progress = 100
    job.error_json = None


def mark_job_retry_queued(session: Session, job: ProcessingJob, retry_count: int) -> None:
    """Return a started job to QUEUED so Celery autoretry can actually execute it again."""
    if job.status != "PROCESSING":
        raise InvalidJobTransition(f"Cannot retry a {job.status} job.")
    current_retry_count = job.retry_count or 0
    if retry_count <= current_retry_count:
        raise InvalidJobTransition("Retry count must increase monotonically.")
    job.status = "QUEUED"
    job.progress = 0
    job.retry_count = retry_count
    job.error_json = None


def mark_job_failed(session: Session, job: ProcessingJob, error: str) -> None:
    if job.status != "PROCESSING":
        raise InvalidJobTransition(f"Cannot fail a {job.status} job.")
    job.status = "FAILED"
    # Worker exceptions can contain implementation or credential details; keep only a safe signal.
    job.error_json = {"message": "Processing failed."}


def mark_job_cancelled(session: Session, job: ProcessingJob) -> None:
    """Cancel only work that has not started; active workers must finish safely."""
    if job.status != "QUEUED":
        raise InvalidJobTransition(f"Cannot cancel a {job.status} job.")
    job.status = "CANCELLED"
    job.progress = 0

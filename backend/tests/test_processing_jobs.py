import uuid

import pytest

from app.models import ProcessingJob
from app.services.processing_jobs import (
    InvalidJobTransition,
    mark_job_completed,
    mark_job_failed,
    mark_job_processing,
    mark_job_cancelled,
    mark_job_retry_queued,
)


def test_processing_job_state_helpers_preserve_failure_details() -> None:
    job = ProcessingJob(
        project_id=uuid.uuid4(),
        job_type="FILE_REGISTERED",
        idempotency_key="file:test:registration",
        status="QUEUED",
    )

    mark_job_processing(None, job)
    assert (job.status, job.progress) == ("PROCESSING", 1)
    mark_job_failed(None, job, "worker unavailable")
    assert job.status == "FAILED"
    assert job.error_json == {"message": "Processing failed."}
    with pytest.raises(InvalidJobTransition):
        mark_job_completed(None, job)

    retry = ProcessingJob(
        project_id=uuid.uuid4(),
        job_type="FILE_REGISTERED",
        idempotency_key="file:test:retry",
        status="QUEUED",
    )
    mark_job_processing(None, retry)
    mark_job_completed(None, retry)
    assert (retry.status, retry.progress, retry.error_json) == ("COMPLETED", 100, None)


def test_queued_jobs_can_be_cancelled_without_touching_started_work() -> None:
    job = ProcessingJob(project_id=uuid.uuid4(), job_type="PARCEL_IMPORT", idempotency_key="geoai:cancel", status="QUEUED")
    mark_job_cancelled(None, job)
    assert (job.status, job.progress) == ("CANCELLED", 0)
    with pytest.raises(InvalidJobTransition):
        mark_job_cancelled(None, job)


def test_processing_job_retry_transition_is_monotonic_and_retriable() -> None:
    job = ProcessingJob(
        project_id=uuid.uuid4(),
        job_type="DOCUMENT_AI_PROCESS",
        idempotency_key="document:test:retry",
        status="QUEUED",
    )
    mark_job_processing(None, job)
    mark_job_retry_queued(None, job, 1)
    assert (job.status, job.progress, job.retry_count, job.error_json) == ("QUEUED", 0, 1, None)

    mark_job_processing(None, job)
    with pytest.raises(InvalidJobTransition):
        mark_job_retry_queued(None, job, 1)

    mark_job_retry_queued(None, job, 2)
    mark_job_processing(None, job)
    mark_job_failed(None, job, "database password=do-not-leak")
    assert job.status == "FAILED"
    assert job.retry_count == 2
    assert job.error_json == {"message": "Processing failed."}

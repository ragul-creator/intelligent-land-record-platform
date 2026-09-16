import uuid

import pytest

from app.models import ProcessingJob
from app.services.processing_jobs import (
    InvalidJobTransition,
    mark_job_completed,
    mark_job_failed,
    mark_job_processing,
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

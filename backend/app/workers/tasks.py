"""Minimal, database-backed Celery task foundation without AI processing."""

import uuid

from app.core.database import SessionLocal
from app.audit.service import record_audit
from app.models import ProcessingJob
from app.services.processing_jobs import mark_job_completed, mark_job_failed, mark_job_processing
from app.workers.celery_app import celery_app


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_file_registration(self, job_id: str) -> None:
    """Exercise worker persistence transitions for a completed file registration."""
    with SessionLocal() as session:
        job = session.get(ProcessingJob, uuid.UUID(job_id))
        if job is None or job.status != "QUEUED":
            return
        try:
            job.retry_count = self.request.retries
            mark_job_processing(session, job)
            record_audit(session, "processing_job.started", "processing_job", job.id, project_id=job.project_id)
            # Future OCR/GeoAI services replace this deliberately minimal handoff task.
            mark_job_completed(session, job)
            record_audit(session, "processing_job.completed", "processing_job", job.id, project_id=job.project_id)
            session.commit()
        except Exception as error:
            session.rollback()
            job = session.get(ProcessingJob, uuid.UUID(job_id))
            if job is not None:
                mark_job_failed(session, job, str(error))
                record_audit(session, "processing_job.failed", "processing_job", job.id, project_id=job.project_id)
                session.commit()
            raise

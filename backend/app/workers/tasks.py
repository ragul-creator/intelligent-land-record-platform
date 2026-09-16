"""Minimal, database-backed Celery task foundation without AI processing."""

import uuid

from app.core.database import SessionLocal
from app.audit.service import record_audit
from app.models import GeoAIJob, ProcessingJob
from app.services.geoai import GeoAIServiceError, persist_parcel_import
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


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_geoai_parcel_import(self, geoai_job_id: str) -> None:
    """Persist one C.4 parcel-import result as immutable PostGIS version 1."""
    job_uuid = uuid.UUID(geoai_job_id)
    with SessionLocal() as session:
        geoai_job = session.get(GeoAIJob, job_uuid)
        processing_job = session.get(ProcessingJob, job_uuid)
        if geoai_job is None or processing_job is None or processing_job.status != "QUEUED":
            return
        try:
            processing_job.retry_count = self.request.retries
            mark_job_processing(session, processing_job)
            record_audit(session, "geoai.job_processing", "geoai_job", geoai_job.id, project_id=geoai_job.project_id)
            session.commit()

            parcel = persist_parcel_import(session, geoai_job.project_id, geoai_job.parameters_json)
            session.flush()
            geoai_job.output_refs_json = {"parcel_id": str(parcel.id), "geometry_version": 1}
            mark_job_completed(session, processing_job)
            record_audit(session, "parcel.created", "parcel", parcel.id, project_id=geoai_job.project_id)
            record_audit(session, "parcel.geometry_version_created", "parcel_geometry_version", None, project_id=geoai_job.project_id, metadata={"parcel_id": str(parcel.id), "version": 1})
            record_audit(session, "geoai.job_completed", "geoai_job", geoai_job.id, project_id=geoai_job.project_id)
            session.commit()
        except Exception as error:
            session.rollback()
            processing_job = session.get(ProcessingJob, job_uuid)
            geoai_job = session.get(GeoAIJob, job_uuid)
            if processing_job is not None and processing_job.status == "PROCESSING":
                mark_job_failed(session, processing_job, str(error))
                if geoai_job is not None:
                    record_audit(session, "geoai.job_failed", "geoai_job", geoai_job.id, project_id=geoai_job.project_id)
                session.commit()
            if isinstance(error, GeoAIServiceError):
                return
            raise

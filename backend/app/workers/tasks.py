"""Database-backed Celery tasks for registered files, GeoAI, and Document AI."""

import uuid
import tempfile
from pathlib import Path
from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.storage import get_storage_service
from app.audit.service import record_audit
from app.models import Document, DocumentProcessingJob, DocumentOcrResultRecord, File, GeoAIJob, ProcessingJob
from app.services.geoai import GeoAIServiceError, persist_parcel_import
from app.services.documents import extraction_from_records, persist_extraction, persist_ocr_result, persist_validation
from app.services.processing_jobs import mark_job_completed, mark_job_failed, mark_job_processing, mark_job_retry_queued
from app.services.review import create_review_task
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
            session.commit()
            # Future OCR/GeoAI services replace this deliberately minimal handoff task.
            mark_job_completed(session, job)
            record_audit(session, "processing_job.completed", "processing_job", job.id, project_id=job.project_id)
            session.commit()
        except Exception as error:
            session.rollback()
            job = session.get(ProcessingJob, uuid.UUID(job_id))
            if job is not None and job.status == "PROCESSING":
                if self.request.retries < self.max_retries:
                    mark_job_retry_queued(session, job, self.request.retries + 1)
                    record_audit(session, "processing_job.retry_queued", "processing_job", job.id, project_id=job.project_id, metadata={"retry_count": job.retry_count})
                else:
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
                if isinstance(error, GeoAIServiceError) or self.request.retries >= self.max_retries:
                    mark_job_failed(session, processing_job, str(error))
                    if geoai_job is not None:
                        record_audit(session, "geoai.job_failed", "geoai_job", geoai_job.id, project_id=geoai_job.project_id)
                else:
                    mark_job_retry_queued(session, processing_job, self.request.retries + 1)
                    if geoai_job is not None:
                        record_audit(session, "geoai.job_retry_queued", "geoai_job", geoai_job.id, project_id=geoai_job.project_id, metadata={"retry_count": processing_job.retry_count})
                session.commit()
            if isinstance(error, GeoAIServiceError):
                return
            raise


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def process_document_ai(self, job_id: str) -> None:
    """Run F.1 -> F.2 -> F.3 without exposing or mutating the source object."""
    job_uuid = uuid.UUID(job_id)
    with SessionLocal() as session:
        job = session.get(ProcessingJob, job_uuid)
        detail = session.get(DocumentProcessingJob, job_uuid)
        if job is None or detail is None or job.status != "QUEUED":
            return
        document = session.get(Document, detail.document_id)
        if document is None:
            return
        try:
            job.retry_count = self.request.retries
            mark_job_processing(session, job)
            document.status = "PROCESSING"
            record_audit(session, "document.processing_started", "document", document.id, project_id=document.project_id, metadata={"processing_job_id": str(job.id)})
            session.commit()

            file = session.get(File, document.file_id)
            if file is None:
                raise RuntimeError("Document source artifact is unavailable.")
            source_bytes = get_storage_service().read_private_object(file.storage_key)
            suffix = Path(file.original_name).suffix or ".bin"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
                temporary.write(source_bytes)
                source_path = Path(temporary.name)
            try:
                from ai.document_ai.extraction import extract_land_record_fields
                from ai.document_ai.pipeline import OcrPipeline

                result = OcrPipeline().process(source_path, source_id=str(document.id), languages=detail.requested_languages_json, allowed_root=source_path.parent)
            finally:
                source_path.unlink(missing_ok=True)

            ocr = persist_ocr_result(session, document=document, job=job, detail=detail, result=result)
            extraction = extract_land_record_fields(result)
            persist_extraction(session, document=document, ocr=ocr, extraction=extraction)
            document.status = "EXTRACTED"
            document.status = "VALIDATING"
            validation, result_validation = persist_validation(session, document=document, ocr=ocr, job=job, extraction=extraction)
            recommendation = result_validation.review_recommendation
            if recommendation and recommendation.required:
                if validation.review_task_id is None:
                    recommendation_metadata = result_validation.to_dict()["review_recommendation"]["metadata"]
                    review = create_review_task(
                        session, project_id=document.project_id, queue_type="DOCUMENT", target_type="LAND_RECORD",
                        target_id=document.id, severity=recommendation.severity, summary=recommendation.summary,
                        source_refs=list(recommendation.source_refs), blocking_issue_count=recommendation.blocking_issue_count,
                        metadata={**recommendation_metadata, "document_id": str(document.id), "validation_result_id": str(validation.id), "validation_version": validation.version},
                    )
                    validation.review_task_id = review.id
                    record_audit(session, "document.review_required", "document", document.id, project_id=document.project_id, metadata={"review_task_id": str(review.id), "validation_result_id": str(validation.id)})
                document.status = "REVIEW_REQUIRED"
            else:
                document.status = "VALIDATED"
                record_audit(session, "document.validated", "document", document.id, project_id=document.project_id, metadata={"validation_result_id": str(validation.id)})
            detail.output_refs_json = {"ocr_result_id": str(ocr.id), "validation_result_id": str(validation.id), "document_status": document.status}
            mark_job_completed(session, job)
            session.commit()
        except Exception:
            session.rollback()
            job = session.get(ProcessingJob, job_uuid)
            document = session.get(Document, detail.document_id) if detail else None
            if job is not None and job.status == "PROCESSING":
                if self.request.retries < self.max_retries:
                    mark_job_retry_queued(session, job, self.request.retries + 1)
                    if document is not None:
                        document.status = "QUEUED"
                        record_audit(session, "document.processing_retry_queued", "document", document.id, project_id=document.project_id, metadata={"processing_job_id": str(job.id), "retry_count": job.retry_count})
                else:
                    mark_job_failed(session, job, "Document AI processing failed")
                    if document is not None:
                        document.status = "FAILED"
                        record_audit(session, "document.processing_failed", "document", document.id, project_id=document.project_id, metadata={"processing_job_id": str(job.id)})
                session.commit()
            raise


@celery_app.task(bind=True, autoretry_for=(Exception,), retry_backoff=True, max_retries=3)
def revalidate_document(self, job_id: str) -> None:
    """Apply F.3 to persisted candidate evidence plus append-only corrections."""
    job_uuid = uuid.UUID(job_id)
    with SessionLocal() as session:
        job = session.get(ProcessingJob, job_uuid)
        detail = session.get(DocumentProcessingJob, job_uuid)
        if job is None or detail is None or detail.job_type != "DOCUMENT_REVALIDATE" or job.status != "QUEUED":
            return
        document = session.get(Document, detail.document_id)
        if document is None:
            return
        try:
            mark_job_processing(session, job)
            document.status = "VALIDATING"
            session.commit()
            ocr = session.scalar(
                select(DocumentOcrResultRecord)
                .where(DocumentOcrResultRecord.document_id == document.id)
                .order_by(DocumentOcrResultRecord.version.desc())
            )
            if ocr is None:
                raise RuntimeError("Persisted OCR result is unavailable.")
            extraction = extraction_from_records(session, document_id=document.id, ocr=ocr, include_corrections=True)
            validation, result_validation = persist_validation(session, document=document, ocr=ocr, job=job, extraction=extraction)
            recommendation = result_validation.review_recommendation
            if recommendation and recommendation.required:
                if validation.review_task_id is None:
                    recommendation_metadata = result_validation.to_dict()["review_recommendation"]["metadata"]
                    review = create_review_task(session, project_id=document.project_id, queue_type="DOCUMENT", target_type="LAND_RECORD", target_id=document.id, severity=recommendation.severity, summary=recommendation.summary, source_refs=list(recommendation.source_refs), blocking_issue_count=recommendation.blocking_issue_count, metadata={**recommendation_metadata, "document_id": str(document.id), "validation_result_id": str(validation.id), "validation_version": validation.version})
                    validation.review_task_id = review.id
                document.status = "REVIEW_REQUIRED"
            else:
                document.status = "VALIDATED"
            detail.output_refs_json = {"validation_result_id": str(validation.id), "document_status": document.status}
            mark_job_completed(session, job)
            session.commit()
        except Exception:
            session.rollback()
            job = session.get(ProcessingJob, job_uuid)
            if job is not None and job.status == "PROCESSING":
                document = session.get(Document, detail.document_id)
                if self.request.retries < self.max_retries:
                    mark_job_retry_queued(session, job, self.request.retries + 1)
                    if document is not None:
                        document.status = "VALIDATING"
                        record_audit(session, "document.revalidation_retry_queued", "document", document.id, project_id=document.project_id, metadata={"processing_job_id": str(job.id), "retry_count": job.retry_count})
                else:
                    mark_job_failed(session, job, "Document revalidation failed")
                    if document is not None:
                        document.status = "FAILED"
                        record_audit(session, "document.processing_failed", "document", document.id, project_id=document.project_id, metadata={"processing_job_id": str(job.id)})
                session.commit()
            raise

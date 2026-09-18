"""Persistence and orchestration helpers for the narrow Phase F.4 workflow."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai.document_ai.extraction import DocumentExtractionResult, ExtractedFieldCandidate, FieldEvidence
from ai.document_ai.models import BoundingBox, DocumentOcrResult
from ai.document_ai.validation import DocumentValidationResult, validate_document_extraction
from app.audit.service import record_audit
from app.models import (
    Document,
    DocumentExtractedField,
    DocumentFieldCorrection,
    DocumentOcrResultRecord,
    DocumentProcessingJob,
    DocumentValidationResultRecord,
    ProcessingJob,
)
from app.services.processing_jobs import create_or_get_job


class DocumentWorkflowError(ValueError):
    pass


def utc_now() -> datetime:
    return datetime.now(UTC)


def active_document_job(session: Session, document_id: uuid.UUID) -> tuple[DocumentProcessingJob, ProcessingJob] | None:
    row = session.execute(
        select(DocumentProcessingJob, ProcessingJob)
        .join(ProcessingJob, ProcessingJob.id == DocumentProcessingJob.id)
        .where(DocumentProcessingJob.document_id == document_id, ProcessingJob.status.in_(("QUEUED", "PROCESSING")))
        .order_by(DocumentProcessingJob.created_at.desc())
    ).first()
    return (row[0], row[1]) if row else None


def queue_document_job(
    session: Session, *, document: Document, actor_id: uuid.UUID, languages: list[str], reprocess: bool = False,
) -> tuple[DocumentProcessingJob, ProcessingJob, bool]:
    active = active_document_job(session, document.id)
    if active:
        return active[0], active[1], False
    if not reprocess and document.status not in {"UPLOADED", "FAILED"}:
        raise DocumentWorkflowError("The document must be uploaded or failed before processing.")
    version = (session.scalar(select(func.max(DocumentOcrResultRecord.version)).where(DocumentOcrResultRecord.document_id == document.id)) or 0) + 1
    key = f"document:{document.id}:ocr:{version}"
    job, created = create_or_get_job(session, document.project_id, "DOCUMENT_AI_PROCESS", key)
    detail = session.get(DocumentProcessingJob, job.id)
    if created:
        detail = DocumentProcessingJob(
            id=job.id, document_id=document.id, requested_languages_json=languages,
            processing_version=version, job_type="DOCUMENT_AI_PROCESS", requested_by_user_id=actor_id,
        )
        session.add(detail)
        document.status = "QUEUED"
        record_audit(session, "document.reprocess_requested" if reprocess else "document.processing_queued", "document", document.id, actor_id=actor_id, project_id=document.project_id, metadata={"processing_job_id": str(job.id), "version": version})
    if detail is None:
        raise DocumentWorkflowError("The idempotency key is owned by another workflow.")
    return detail, job, created


def persist_ocr_result(session: Session, *, document: Document, job: ProcessingJob, detail: DocumentProcessingJob, result: DocumentOcrResult) -> DocumentOcrResultRecord:
    existing = session.scalar(select(DocumentOcrResultRecord).where(DocumentOcrResultRecord.processing_job_id == job.id))
    if existing is not None:
        return existing
    row = DocumentOcrResultRecord(
        document_id=document.id, processing_job_id=job.id, version=detail.processing_version,
        status=result.status, requested_languages_json=list(result.requested_languages),
        project_tested_languages_json=list(result.project_tested_languages), engine=result.engine,
        engine_version=result.engine_version, model_version=result.model_version, page_count=len(result.pages),
        confidence=_mean(page.confidence for page in result.pages), payload_json=result.to_dict(), processed_at=result.processed_at,
    )
    session.add(row)
    session.flush()
    record_audit(session, "document.ocr_completed", "document", document.id, project_id=document.project_id, metadata={"ocr_result_id": str(row.id), "version": row.version})
    return row


def persist_extraction(session: Session, *, document: Document, ocr: DocumentOcrResultRecord, extraction: DocumentExtractionResult) -> None:
    if session.scalar(select(DocumentExtractedField.id).where(DocumentExtractedField.ocr_result_id == ocr.id)) is not None:
        return
    index = 0
    for candidates in extraction.fields.values():
        for candidate in candidates:
            bbox = candidate.source.bounding_box
            session.add(DocumentExtractedField(
                document_id=document.id, ocr_result_id=ocr.id, candidate_index=index, field_name=candidate.field_name,
                original_value=candidate.original_value, normalized_value_json=candidate.normalized_value,
                confidence=candidate.confidence, page_number=candidate.source.page_number,
                bounding_box_json=None if bbox is None else {"left": bbox.left, "top": bbox.top, "width": bbox.width, "height": bbox.height},
                source_id=candidate.source.source_id, model_version=candidate.model_version,
                extractor_version=candidate.extractor_version, processed_at=candidate.processed_at,
            ))
            index += 1
    record_audit(session, "document.extraction_completed", "document", document.id, project_id=document.project_id, metadata={"ocr_result_id": str(ocr.id), "candidate_count": index})


def extraction_from_records(session: Session, *, document_id: uuid.UUID, ocr: DocumentOcrResultRecord, include_corrections: bool = False) -> DocumentExtractionResult:
    records = list(session.scalars(select(DocumentExtractedField).where(DocumentExtractedField.document_id == document_id, DocumentExtractedField.ocr_result_id == ocr.id).order_by(DocumentExtractedField.candidate_index)))
    fields: dict[str, list[ExtractedFieldCandidate]] = {}
    for record in records:
        value = record.original_value
        normalized = record.normalized_value_json
        if include_corrections:
            correction = session.scalar(select(DocumentFieldCorrection).where(DocumentFieldCorrection.extracted_field_id == record.id).order_by(DocumentFieldCorrection.version.desc()))
            if correction:
                value = correction.corrected_value
                normalized = correction.corrected_value
        bbox = BoundingBox(**record.bounding_box_json) if record.bounding_box_json else None
        fields.setdefault(record.field_name, []).append(ExtractedFieldCandidate(
            field_name=record.field_name, original_value=value, normalized_value=normalized, confidence=record.confidence,
            source=FieldEvidence(source_id=record.source_id, page_number=record.page_number, bounding_box=bbox),
            model_version=record.model_version, extractor_version=record.extractor_version, processed_at=record.processed_at,
        ))
    payload = ocr.payload_json
    return DocumentExtractionResult(source_id=str(payload.get("source_id", document_id)), fields={name: tuple(values) for name, values in fields.items()}, extraction_version=records[0].extractor_version if records else "document-extraction-mvp-v1", processed_at=utc_now())


def persist_validation(session: Session, *, document: Document, ocr: DocumentOcrResultRecord, job: ProcessingJob, extraction: DocumentExtractionResult) -> tuple[DocumentValidationResultRecord, DocumentValidationResult]:
    existing = session.scalar(select(DocumentValidationResultRecord).where(DocumentValidationResultRecord.processing_job_id == job.id))
    validation = validate_document_extraction(extraction)
    if existing is not None:
        return existing, validation
    version = (session.scalar(select(func.max(DocumentValidationResultRecord.version)).where(DocumentValidationResultRecord.document_id == document.id)) or 0) + 1
    payload = validation.to_dict()
    row = DocumentValidationResultRecord(
        document_id=document.id, ocr_result_id=ocr.id, processing_job_id=job.id, version=version,
        status=validation.status.value, validation_version=validation.validation_version,
        report_json=payload["validation_report"], confidence_summary_json=payload["confidence_summary"],
        checks_json=validation.checks, processed_at=validation.processed_at,
    )
    session.add(row)
    session.flush()
    record_audit(session, "document.validation_completed", "document", document.id, project_id=document.project_id, metadata={"validation_result_id": str(row.id), "status": row.status})
    return row, validation


def create_correction(session: Session, *, document: Document, field: DocumentExtractedField, value: str, reason: str, actor_id: uuid.UUID) -> DocumentFieldCorrection:
    version = (session.scalar(select(func.max(DocumentFieldCorrection.version)).where(DocumentFieldCorrection.extracted_field_id == field.id)) or 0) + 1
    correction = DocumentFieldCorrection(document_id=document.id, extracted_field_id=field.id, version=version, corrected_value=value, reason=reason, created_by_user_id=actor_id)
    session.add(correction)
    session.flush()
    record_audit(session, "document.field_corrected", "document_field_correction", correction.id, actor_id=actor_id, project_id=document.project_id, metadata={"document_id": str(document.id), "field_name": field.field_name, "extracted_field_id": str(field.id), "version": version})
    return correction


def queue_correction_revalidation(session: Session, *, document: Document, correction: DocumentFieldCorrection, actor_id: uuid.UUID) -> tuple[DocumentProcessingJob, ProcessingJob, bool]:
    """Revalidate versioned corrections without rerunning or replacing source OCR."""
    active = active_document_job(session, document.id)
    if active:
        return active[0], active[1], False
    ocr_version = session.scalar(select(func.max(DocumentOcrResultRecord.version)).where(DocumentOcrResultRecord.document_id == document.id))
    if ocr_version is None:
        raise DocumentWorkflowError("A persisted OCR result is required before revalidation.")
    job, created = create_or_get_job(session, document.project_id, "DOCUMENT_REVALIDATE", f"document:{document.id}:correction:{correction.id}")
    detail = session.get(DocumentProcessingJob, job.id)
    if created:
        detail = DocumentProcessingJob(id=job.id, document_id=document.id, requested_languages_json=[], processing_version=ocr_version, job_type="DOCUMENT_REVALIDATE", requested_by_user_id=actor_id)
        session.add(detail)
        document.status = "VALIDATING"
        record_audit(session, "document.revalidation_queued", "document", document.id, actor_id=actor_id, project_id=document.project_id, metadata={"processing_job_id": str(job.id), "correction_id": str(correction.id)})
    if detail is None:
        raise DocumentWorkflowError("The idempotency key is owned by another workflow.")
    return detail, job, created


def _mean(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return sum(known) / len(known) if known else None

"""Project-scoped upload, processing, and evidence APIs for Phase F.4."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File as UploadFileField, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ai.document_ai.languages import parse_language_configuration
from ai.document_ai.errors import OcrLanguageConfigurationError
from app.audit.service import record_audit
from app.core.auth import get_current_user
from app.core.config import get_settings
from app.core.database import get_db_session
from app.core.errors import ApiError, not_found
from app.core.storage import PrivateObjectStorage, StorageObjectAlreadyExistsError, get_storage_service
from app.models import Document, DocumentExtractedField, DocumentFieldCorrection, DocumentOcrResultRecord, DocumentProcessingJob, DocumentValidationResultRecord, File, ProcessingJob, User
from app.schemas.common import PageMetadata
from app.schemas.documents import (
    DocumentCorrectionRequest, DocumentDetailResponse, DocumentJobResponse, DocumentListResponse,
    DocumentProcessRequest, DocumentSummaryResponse, ExtractedFieldResponse, FieldCorrectionResponse,
    FieldsResponse, OcrResultResponse, SourceUrlResponse, ValidationResultResponse,
)
from app.services.documents import DocumentWorkflowError, create_correction, queue_correction_revalidation, queue_document_job
from app.services.file_policy import ALLOWED_CONTENT_TYPES, FileCategory, MAX_UPLOAD_SIZE_BYTES
from app.services.project_access import get_project_for_user


router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])


def _document(session: Session, project_id: uuid.UUID, document_id: uuid.UUID) -> Document:
    document = session.scalar(select(Document).where(Document.id == document_id, Document.project_id == project_id))
    if document is None:
        raise not_found("DOCUMENT_NOT_FOUND", "The requested document was not found.")
    return document


def _latest_ocr(session: Session, document_id: uuid.UUID) -> DocumentOcrResultRecord | None:
    return session.scalar(select(DocumentOcrResultRecord).where(DocumentOcrResultRecord.document_id == document_id).order_by(DocumentOcrResultRecord.version.desc()))


def _latest_validation(session: Session, document_id: uuid.UUID) -> DocumentValidationResultRecord | None:
    return session.scalar(select(DocumentValidationResultRecord).where(DocumentValidationResultRecord.document_id == document_id).order_by(DocumentValidationResultRecord.version.desc()))


def _summary(session: Session, document: Document) -> DocumentSummaryResponse:
    source = session.get(File, document.file_id)
    job_id = session.scalar(select(DocumentProcessingJob.id).where(DocumentProcessingJob.document_id == document.id).order_by(DocumentProcessingJob.created_at.desc()))
    if source is None:
        raise RuntimeError("Document source metadata is unavailable.")
    return DocumentSummaryResponse(id=document.id, project_id=document.project_id, filename=source.original_name, content_type=source.mime_type, size_bytes=source.size_bytes, status=document.status, latest_processing_job_id=job_id, uploaded_at=document.created_at, updated_at=document.updated_at)


def _ocr_response(row: DocumentOcrResultRecord) -> OcrResultResponse:
    return OcrResultResponse(id=row.id, version=row.version, status=row.status, requested_languages=row.requested_languages_json, project_tested_languages=row.project_tested_languages_json, engine=row.engine, engine_version=row.engine_version, model_version=row.model_version, page_count=row.page_count, confidence=row.confidence, payload=row.payload_json, processed_at=row.processed_at)


def _validation_response(row: DocumentValidationResultRecord) -> ValidationResultResponse:
    return ValidationResultResponse(id=row.id, version=row.version, status=row.status, validation_version=row.validation_version, report=row.report_json, confidence_summary=row.confidence_summary_json, checks=row.checks_json, review_task_id=row.review_task_id, processed_at=row.processed_at)


@router.post("", response_model=DocumentSummaryResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    project_id: uuid.UUID, upload: UploadFile = UploadFileField(...), session: Session = Depends(get_db_session),
    storage: PrivateObjectStorage = Depends(get_storage_service), user: User = Depends(get_current_user),
) -> DocumentSummaryResponse:
    project = get_project_for_user(session, user, project_id, "document:upload")
    content_type = (upload.content_type or "").lower().split(";", 1)[0]
    filename = upload.filename or ""
    suffix = Path(filename).suffix.lower()
    allowed_suffixes = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}
    if content_type not in ALLOWED_CONTENT_TYPES[FileCategory.DOCUMENT] or suffix not in allowed_suffixes:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_CONTENT, "DOCUMENT_TYPE_UNSUPPORTED", "Only PDF, PNG, JPG/JPEG, and TIFF/TIF document files are supported.")
    if not filename or Path(filename).name != filename:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_CONTENT, "DOCUMENT_FILENAME_INVALID", "The document filename is invalid.")
    upload.file.seek(0, 2)
    size_bytes = upload.file.tell()
    upload.file.seek(0)
    if size_bytes <= 0 or size_bytes > MAX_UPLOAD_SIZE_BYTES:
        raise ApiError(status.HTTP_422_UNPROCESSABLE_CONTENT, "DOCUMENT_SIZE_INVALID", "The document size is outside the supported upload limit.")
    digest = hashlib.sha256()
    while chunk := upload.file.read(1024 * 1024):
        digest.update(chunk)
    upload.file.seek(0)
    file_id = uuid.uuid4()
    storage_key = storage.generate_storage_key(project.id, file_id, filename)
    try:
        storage.put_private_object(storage_key, upload.file, content_type=content_type, size_bytes=size_bytes, checksum=digest.hexdigest())
    except StorageObjectAlreadyExistsError as error:
        raise ApiError(status.HTTP_409_CONFLICT, "DOCUMENT_STORAGE_CONFLICT", "A document object already exists at this immutable key.") from error
    source = File(id=file_id, project_id=project.id, original_name=filename, category="DOCUMENT", mime_type=content_type, size_bytes=size_bytes, sha256=digest.hexdigest(), storage_key=storage_key, status="UPLOADED")
    # File and Document are linked by a scalar UUID rather than an ORM
    # relationship, so persist the source before its foreign-key consumer.
    session.add(source)
    session.flush()
    document = Document(project_id=source.project_id, file_id=source.id, uploaded_by_user_id=user.id, status="UPLOADED")
    session.add(document)
    session.flush()
    record_audit(session, "document.uploaded", "document", document.id, actor_id=user.id, project_id=project.id, metadata={"file_id": str(source.id), "content_type": content_type, "size_bytes": size_bytes})
    session.commit()
    return _summary(session, document)


@router.get("", response_model=DocumentListResponse)
def list_documents(project_id: uuid.UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0), session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> DocumentListResponse:
    get_project_for_user(session, user, project_id, "document:read")
    total = session.scalar(select(func.count(Document.id)).where(Document.project_id == project_id)) or 0
    documents = list(session.scalars(select(Document).where(Document.project_id == project_id).order_by(Document.created_at.desc(), Document.id).limit(limit).offset(offset)))
    return DocumentListResponse(items=[_summary(session, document) for document in documents], page=PageMetadata(limit=limit, offset=offset, total=total))


@router.get("/{document_id}", response_model=DocumentDetailResponse)
def get_document(project_id: uuid.UUID, document_id: uuid.UUID, session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> DocumentDetailResponse:
    get_project_for_user(session, user, project_id, "document:read")
    document = _document(session, project_id, document_id)
    summary = _summary(session, document)
    ocr, validation = _latest_ocr(session, document.id), _latest_validation(session, document.id)
    return DocumentDetailResponse(**summary.model_dump(), latest_ocr=_ocr_response(ocr) if ocr else None, latest_validation=_validation_response(validation) if validation else None)


@router.get("/{document_id}/source-url", response_model=SourceUrlResponse)
def source_url(project_id: uuid.UUID, document_id: uuid.UUID, session: Session = Depends(get_db_session), storage: PrivateObjectStorage = Depends(get_storage_service), user: User = Depends(get_current_user)) -> SourceUrlResponse:
    get_project_for_user(session, user, project_id, "document:read")
    document = _document(session, project_id, document_id)
    source = session.get(File, document.file_id)
    if source is None:
        raise not_found("DOCUMENT_SOURCE_NOT_FOUND", "The document source was not found.")
    record_audit(session, "document.source_url_requested", "document", document.id, actor_id=user.id, project_id=project_id)
    session.commit()
    return SourceUrlResponse(document_id=document.id, source_url=storage.presign_download(source.storage_key), expires_in_seconds=get_settings().signed_url_expiry_seconds)


def _queue(project_id: uuid.UUID, document_id: uuid.UUID, request: DocumentProcessRequest, reprocess: bool, session: Session, user: User) -> DocumentJobResponse:
    permission = "document:reprocess" if reprocess else "document:process"
    get_project_for_user(session, user, project_id, permission)
    document = _document(session, project_id, document_id)
    try:
        languages = list(parse_language_configuration(request.languages))
        detail, job, created = queue_document_job(session, document=document, actor_id=user.id, languages=languages, reprocess=reprocess)
    except (DocumentWorkflowError, OcrLanguageConfigurationError) as error:
        raise ApiError(status.HTTP_409_CONFLICT, "DOCUMENT_PROCESSING_UNAVAILABLE", str(error)) from error
    session.commit()
    if created:
        from app.workers.tasks import process_document_ai
        process_document_ai.delay(str(job.id))
    return DocumentJobResponse(id=job.id, document_id=document.id, status=job.status, progress=job.progress, processing_version=detail.processing_version)


@router.post("/{document_id}/process", response_model=DocumentJobResponse, status_code=status.HTTP_202_ACCEPTED)
def process_document(project_id: uuid.UUID, document_id: uuid.UUID, request: DocumentProcessRequest, session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> DocumentJobResponse:
    return _queue(project_id, document_id, request, False, session, user)


@router.post("/{document_id}/reprocess", response_model=DocumentJobResponse, status_code=status.HTTP_202_ACCEPTED)
def reprocess_document(project_id: uuid.UUID, document_id: uuid.UUID, request: DocumentProcessRequest, session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> DocumentJobResponse:
    return _queue(project_id, document_id, request, True, session, user)


@router.get("/{document_id}/ocr", response_model=OcrResultResponse)
def get_ocr(project_id: uuid.UUID, document_id: uuid.UUID, session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> OcrResultResponse:
    get_project_for_user(session, user, project_id, "document:read")
    row = _latest_ocr(session, _document(session, project_id, document_id).id)
    if row is None:
        raise not_found("DOCUMENT_OCR_NOT_FOUND", "No OCR result is available for this document.")
    return _ocr_response(row)


@router.get("/{document_id}/fields", response_model=FieldsResponse)
def get_fields(project_id: uuid.UUID, document_id: uuid.UUID, session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> FieldsResponse:
    get_project_for_user(session, user, project_id, "field:read")
    document = _document(session, project_id, document_id)
    ocr = _latest_ocr(session, document.id)
    if ocr is None:
        return FieldsResponse(ocr_result_id=None, fields=[])
    records = list(session.scalars(select(DocumentExtractedField).where(DocumentExtractedField.document_id == document.id, DocumentExtractedField.ocr_result_id == ocr.id).order_by(DocumentExtractedField.candidate_index)))
    fields = []
    for record in records:
        corrections = list(session.scalars(select(DocumentFieldCorrection).where(DocumentFieldCorrection.extracted_field_id == record.id).order_by(DocumentFieldCorrection.version)))
        fields.append(ExtractedFieldResponse(id=record.id, field_name=record.field_name, original_value=record.original_value, normalized_value=record.normalized_value_json, confidence=record.confidence, page_number=record.page_number, bounding_box=record.bounding_box_json, source_id=record.source_id, model_version=record.model_version, extractor_version=record.extractor_version, processed_at=record.processed_at, corrections=[FieldCorrectionResponse(id=item.id, version=item.version, corrected_value=item.corrected_value, reason=item.reason, created_by_user_id=item.created_by_user_id, created_at=item.created_at) for item in corrections]))
    return FieldsResponse(ocr_result_id=ocr.id, fields=fields)


@router.get("/{document_id}/validation", response_model=ValidationResultResponse)
def get_validation(project_id: uuid.UUID, document_id: uuid.UUID, session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> ValidationResultResponse:
    get_project_for_user(session, user, project_id, "validation:run")
    row = _latest_validation(session, _document(session, project_id, document_id).id)
    if row is None:
        raise not_found("DOCUMENT_VALIDATION_NOT_FOUND", "No validation result is available for this document.")
    return _validation_response(row)


@router.post("/{document_id}/fields/{field_id}/corrections", response_model=FieldCorrectionResponse, status_code=status.HTTP_201_CREATED)
def correct_field(project_id: uuid.UUID, document_id: uuid.UUID, field_id: uuid.UUID, request: DocumentCorrectionRequest, session: Session = Depends(get_db_session), user: User = Depends(get_current_user)) -> FieldCorrectionResponse:
    get_project_for_user(session, user, project_id, "field:correct")
    document = _document(session, project_id, document_id)
    field = session.scalar(select(DocumentExtractedField).where(DocumentExtractedField.id == field_id, DocumentExtractedField.document_id == document.id))
    if field is None:
        raise not_found("DOCUMENT_FIELD_NOT_FOUND", "The extracted field was not found.")
    correction = create_correction(session, document=document, field=field, value=request.corrected_value.strip(), reason=request.reason.strip(), actor_id=user.id)
    _, job, created = queue_correction_revalidation(session, document=document, correction=correction, actor_id=user.id)
    session.commit()
    if created:
        from app.workers.tasks import revalidate_document
        revalidate_document.delay(str(job.id))
    return FieldCorrectionResponse(id=correction.id, version=correction.version, corrected_value=correction.corrected_value, reason=correction.reason, created_by_user_id=correction.created_by_user_id, created_at=correction.created_at)

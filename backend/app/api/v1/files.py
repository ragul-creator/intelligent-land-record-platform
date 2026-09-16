"""Private object-storage file registration endpoints for Phase B.2."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.core.auth import (
    get_current_user,
    user_permissions,
)
from app.core.config import get_settings
from app.core.database import get_db_session
from app.core.errors import ApiError, forbidden, not_found
from app.core.storage import (
    PrivateObjectStorage,
    StorageObjectAlreadyExistsError,
    StorageObjectNotFoundError,
    get_storage_service,
)
from app.models import File, ProjectMember, User
from app.schemas.files import (
    CompleteFileRequest,
    CompleteFileResponse,
    DownloadFileResponse,
    PresignFileRequest,
    PresignFileResponse,
)
from app.services.file_policy import upload_permission_for_category
from app.services.processing_jobs import create_or_get_job
from app.services.project_access import get_project_for_user

router = APIRouter(prefix="/files", tags=["files"])


def _get_scoped_file(session: Session, user: User, file_id: uuid.UUID) -> File:
    """Hide files outside the caller's project scope to prevent BOLA enumeration."""
    file = session.scalar(
        select(File)
        .join(ProjectMember, ProjectMember.project_id == File.project_id)
        .where(File.id == file_id, ProjectMember.user_id == user.id)
    )
    if file is None:
        raise not_found("FILE_NOT_FOUND", "The requested file was not found.")
    return file


@router.post("/presign", response_model=PresignFileResponse, status_code=status.HTTP_201_CREATED)
def presign_file_upload(
    request: PresignFileRequest,
    session: Session = Depends(get_db_session),
    storage: PrivateObjectStorage = Depends(get_storage_service),
    user: User = Depends(get_current_user),
) -> PresignFileResponse:
    permission = upload_permission_for_category(request.category)
    get_project_for_user(session, user, request.project_id, permission)

    file_id = uuid.uuid4()
    storage_key = storage.generate_storage_key(request.project_id, file_id, request.filename)
    file = File(
        id=file_id,
        project_id=request.project_id,
        original_name=request.filename,
        category=request.category.value,
        mime_type=request.content_type,
        size_bytes=request.size_bytes,
        sha256=request.sha256.lower() if request.sha256 else None,
        storage_key=storage_key,
        status="PENDING_UPLOAD",
    )
    session.add(file)
    try:
        upload_url, required_headers = storage.presign_upload(
            storage_key, request.content_type, file.sha256
        )
        record_audit(
            session,
            "file.presign_requested",
            "file",
            file.id,
            actor_id=user.id,
            project_id=file.project_id,
        )
        session.commit()
    except StorageObjectAlreadyExistsError as error:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Storage key already exists.") from error
    except Exception:
        session.rollback()
        raise

    return PresignFileResponse(
        file_id=file.id,
        status=file.status,
        upload_url=upload_url,
        required_headers=required_headers,
        expires_in_seconds=get_settings().signed_url_expiry_seconds,
    )


@router.post("/complete", response_model=CompleteFileResponse)
def complete_file_upload(
    request: CompleteFileRequest,
    session: Session = Depends(get_db_session),
    storage: PrivateObjectStorage = Depends(get_storage_service),
    user: User = Depends(get_current_user),
) -> CompleteFileResponse:
    file = _get_scoped_file(session, user, request.file_id)
    permission = upload_permission_for_category(file.category)
    if permission not in user_permissions(session, user.id):
        raise forbidden("FILE_FORBIDDEN", "You are not allowed to complete this upload.")
    if file.status == "UPLOADED":
        job, created = create_or_get_job(
            session,
            project_id=file.project_id,
            job_type="FILE_REGISTERED",
            idempotency_key=f"file:{file.id}:registration",
        )
        if created:
            record_audit(
                session,
                "processing_job.created",
                "processing_job",
                job.id,
                actor_id=user.id,
                project_id=file.project_id,
                metadata={"job_type": job.job_type},
            )
            session.commit()
            from app.workers.tasks import process_file_registration

            process_file_registration.delay(str(job.id))
        return CompleteFileResponse(file_id=file.id, status=file.status, processing_job_id=job.id)
    if file.status != "PENDING_UPLOAD":
        raise ApiError(status.HTTP_409_CONFLICT, "FILE_UNAVAILABLE", "The file upload is unavailable.")

    try:
        object_info = storage.get_object_info(file.storage_key)
    except StorageObjectNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Uploaded object was not found.") from error

    if object_info.size_bytes != file.size_bytes or object_info.content_type.split(";", 1)[0] != file.mime_type:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Uploaded object metadata does not match the registration.")
    if file.sha256 and object_info.metadata.get("sha256", "").lower() != file.sha256:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Uploaded object checksum metadata does not match the registration.")

    file.status = "UPLOADED"
    job, created = create_or_get_job(
        session,
        project_id=file.project_id,
        job_type="FILE_REGISTERED",
        idempotency_key=f"file:{file.id}:registration",
    )
    record_audit(
        session,
        "file.upload_completed",
        "file",
        file.id,
        actor_id=user.id,
        project_id=file.project_id,
    )
    if created:
        record_audit(
            session,
            "processing_job.created",
            "processing_job",
            job.id,
            actor_id=user.id,
            project_id=file.project_id,
            metadata={"job_type": job.job_type},
        )
    session.commit()
    if created:
        from app.workers.tasks import process_file_registration

        process_file_registration.delay(str(job.id))
    return CompleteFileResponse(file_id=file.id, status=file.status, processing_job_id=job.id)


@router.get("/{file_id}/download", response_model=DownloadFileResponse)
def presign_file_download(
    file_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    storage: PrivateObjectStorage = Depends(get_storage_service),
    user: User = Depends(get_current_user),
) -> DownloadFileResponse:
    file = _get_scoped_file(session, user, file_id)
    if "document:read" not in user_permissions(session, user.id):
        raise forbidden("FILE_FORBIDDEN", "You are not allowed to download this file.")
    if file.status != "UPLOADED":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="File is not available for download.")
    download_url = storage.presign_download(file.storage_key)
    record_audit(
        session,
        "file.download_requested",
        "file",
        file.id,
        actor_id=user.id,
        project_id=file.project_id,
    )
    session.commit()
    return DownloadFileResponse(
        file_id=file.id,
        download_url=download_url,
        expires_in_seconds=get_settings().signed_url_expiry_seconds,
    )

"""Private object-storage file registration endpoints for Phase B.2."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.core.auth import (
    get_current_user,
    require_project_access,
    require_project_permission,
    user_permissions,
)
from app.core.config import get_settings
from app.core.database import get_db_session
from app.core.storage import (
    PrivateObjectStorage,
    StorageObjectAlreadyExistsError,
    StorageObjectNotFoundError,
    get_storage_service,
)
from app.models import File, Project, User
from app.schemas.files import (
    CompleteFileRequest,
    CompleteFileResponse,
    DownloadFileResponse,
    PresignFileRequest,
    PresignFileResponse,
)
from app.services.processing_jobs import create_or_get_job

router = APIRouter(prefix="/files", tags=["files"])


@router.post("/presign", response_model=PresignFileResponse, status_code=status.HTTP_201_CREATED)
def presign_file_upload(
    request: PresignFileRequest,
    session: Session = Depends(get_db_session),
    storage: PrivateObjectStorage = Depends(get_storage_service),
    user: User = Depends(get_current_user),
) -> PresignFileResponse:
    permission = "imagery:upload" if request.category == "IMAGERY" else "document:upload"
    require_project_permission(session, user, request.project_id, permission)
    if session.get(Project, request.project_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project was not found.")

    file_id = uuid.uuid4()
    storage_key = storage.generate_storage_key(request.project_id, file_id, request.filename)
    file = File(
        id=file_id,
        project_id=request.project_id,
        original_name=request.filename,
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
    file = session.get(File, request.file_id)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File was not found.")
    require_project_access(session, user, file.project_id)
    if not user_permissions(session, user.id).intersection({"document:upload", "imagery:upload"}):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied.")
    if file.status != "PENDING_UPLOAD":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="File upload is already completed or unavailable.")

    try:
        object_info = storage.get_object_info(file.storage_key)
    except StorageObjectNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Uploaded object was not found.") from error

    if object_info.size_bytes != file.size_bytes or object_info.content_type.split(";", 1)[0] != file.mime_type:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Uploaded object metadata does not match the registration.")
    if file.sha256 and object_info.metadata.get("sha256", "").lower() != file.sha256:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Uploaded object checksum metadata does not match the registration.")

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
    file = session.get(File, file_id)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File was not found.")
    require_project_permission(session, user, file.project_id, "document:read")
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

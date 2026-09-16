"""Pydantic contracts for private file storage endpoints."""

import uuid

from pydantic import BaseModel, Field

from app.services.file_policy import FileCategory, UploadMetadata


class PresignFileRequest(UploadMetadata):
    project_id: uuid.UUID


class PresignFileResponse(BaseModel):
    file_id: uuid.UUID
    status: str
    upload_url: str
    upload_method: str = "PUT"
    required_headers: dict[str, str]
    expires_in_seconds: int


class CompleteFileRequest(BaseModel):
    file_id: uuid.UUID


class CompleteFileResponse(BaseModel):
    file_id: uuid.UUID
    status: str
    processing_job_id: uuid.UUID


class DownloadFileResponse(BaseModel):
    file_id: uuid.UUID
    download_url: str
    expires_in_seconds: int

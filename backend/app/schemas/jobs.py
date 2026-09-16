"""Safe read contracts for persisted processing jobs."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.common import PageMetadata

ProcessingJobStatus = Literal["QUEUED", "PROCESSING", "COMPLETED", "FAILED", "CANCELLED"]


class ProcessingJobResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    job_type: str
    status: ProcessingJobStatus
    progress: int
    retry_count: int
    has_error: bool
    created_at: datetime
    updated_at: datetime


class ProcessingJobListResponse(BaseModel):
    items: list[ProcessingJobResponse]
    page: PageMetadata

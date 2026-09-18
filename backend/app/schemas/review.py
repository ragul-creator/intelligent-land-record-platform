"""Phase E.2 review queue and reviewer-action API contracts."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import PageMetadata


ReviewQueueType = Literal["DOCUMENT", "GIS"]
ReviewSeverity = Literal["INFO", "LOW", "MEDIUM", "HIGH"]
ReviewTaskStatus = Literal["OPEN", "RESOLVED"]
ReviewAction = Literal["APPROVE", "CORRECT", "REJECT", "REPROCESS", "COMMENT", "ESCALATE"]


class ReviewTaskResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    queue_type: ReviewQueueType
    target_type: str
    target_id: uuid.UUID
    severity: ReviewSeverity
    status: ReviewTaskStatus
    summary: str
    source_refs: list[str]
    metadata: dict[str, Any]
    blocking_issue_count: int
    assignee_user_id: uuid.UUID | None
    created_by_user_id: uuid.UUID | None
    escalated: bool
    resolution_action: str | None
    resolved_at: datetime | None
    resolved_by_user_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


class ReviewHistoryEvent(BaseModel):
    action: str
    actor_id: uuid.UUID | None
    metadata: dict[str, Any]
    created_at: datetime


class ReviewTaskDetailResponse(ReviewTaskResponse):
    history: list[ReviewHistoryEvent]


class ReviewTaskListResponse(BaseModel):
    items: list[ReviewTaskResponse]
    page: PageMetadata


class ReviewTaskUpdateRequest(BaseModel):
    action: ReviewAction | None = None
    assignee_user_id: uuid.UUID | None = None
    reason: str | None = Field(default=None, max_length=2000)
    correction_reference: str | None = Field(default=None, max_length=1024)
    reprocess_job_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def require_mutation(self) -> "ReviewTaskUpdateRequest":
        if self.action is None and self.assignee_user_id is None:
            raise ValueError("Provide an action or assignee_user_id.")
        return self

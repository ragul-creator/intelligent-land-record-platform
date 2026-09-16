"""Typed API contracts for the Phase B project foundation."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.permissions import APPLICATION_ROLES
from app.schemas.common import PageMetadata

ProjectState = Literal["ACTIVE", "ARCHIVED"]
ApplicationRole = Literal["ADMIN", "OFFICER", "REVIEWER", "SURVEYOR", "VIEWER"]


class ProjectCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Project name cannot be blank.")
        return normalized


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=10_000)
    state: ProjectState | None = None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Project name cannot be blank.")
        return normalized


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    state: ProjectState
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ProjectListResponse(BaseModel):
    items: list[ProjectResponse]
    page: PageMetadata


class MemberCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    role: ApplicationRole


class MemberUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: ApplicationRole


class ProjectMemberResponse(BaseModel):
    user_id: uuid.UUID
    login_id: str
    full_name: str
    is_active: bool
    role: ApplicationRole
    created_at: datetime


class ProjectMemberListResponse(BaseModel):
    items: list[ProjectMemberResponse]
    page: PageMetadata


class StatusCount(BaseModel):
    status: str
    count: int


class ProjectSummaryResponse(BaseModel):
    project_id: uuid.UUID
    member_count: int
    file_count: int
    processing_job_count: int
    audit_event_count: int
    file_statuses: list[StatusCount]
    processing_job_statuses: list[StatusCount]


class ProjectWorkflowResponse(BaseModel):
    project_id: uuid.UUID
    observed_stages: list[StatusCount]
    canonical_states: list[str]


class AuditLogResponse(BaseModel):
    id: uuid.UUID
    actor_id: uuid.UUID | None
    action: str
    target_type: str
    target_id: uuid.UUID | None
    metadata: dict
    created_at: datetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    page: PageMetadata


assert set(APPLICATION_ROLES) == {"ADMIN", "OFFICER", "REVIEWER", "SURVEYOR", "VIEWER"}

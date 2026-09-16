"""Authentication and current-user API contracts."""

import uuid

from pydantic import AliasChoices, BaseModel, Field


class LoginRequest(BaseModel):
    identifier: str = Field(
        min_length=3,
        max_length=320,
        validation_alias=AliasChoices("identifier", "email"),
    )
    password: str = Field(min_length=1, max_length=1024)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    access_expires_in_seconds: int
    refresh_expires_in_seconds: int


class ProjectMembershipResponse(BaseModel):
    project_id: uuid.UUID
    role: str


class CurrentUserResponse(BaseModel):
    id: uuid.UUID
    login_id: str
    email: str
    full_name: str
    roles: list[str]
    permissions: list[str]
    project_memberships: list[ProjectMembershipResponse]

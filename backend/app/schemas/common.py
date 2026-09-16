"""Small shared response contracts for paginated Phase B APIs."""

from pydantic import BaseModel, Field


class Pagination(BaseModel):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class PageMetadata(BaseModel):
    limit: int
    offset: int
    total: int


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody

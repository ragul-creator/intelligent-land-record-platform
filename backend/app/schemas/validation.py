"""H.2B.3 validation issue API contracts."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from app.schemas.review import ReviewTaskResponse


ValidationIssueType = Literal["DUPLICATE_RECORD", "AREA_MISMATCH"]


class ValidationRunResponse(BaseModel):
    created_count: int
    refreshed_count: int
    open_issue_count: int
    duplicate_record_issue_count: int
    area_mismatch_issue_count: int
    items: list[ReviewTaskResponse]

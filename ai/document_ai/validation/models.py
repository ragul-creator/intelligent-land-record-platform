"""F.3 wrappers around E.1 reports, confidence summaries, and review drafts."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from ai.validation.models import ConfidenceBand, ValidationReport, ValidationStatus


@dataclass(frozen=True, slots=True)
class FieldConfidenceSummary:
    field_name: str
    representative_confidence: float | None
    band: ConfidenceBand
    candidate_count: int
    unknown_confidence_count: int


@dataclass(frozen=True, slots=True)
class DocumentConfidenceSummary:
    value: float | None
    band: ConfidenceBand
    fields: tuple[FieldConfidenceSummary, ...]
    contributing_field_count: int
    missing_field_count: int
    unknown_confidence_field_count: int
    conflict_field_count: int
    policy_version: str
    high_threshold: float
    medium_threshold: float


@dataclass(frozen=True, slots=True)
class DocumentReviewRecommendation:
    """Pure draft for later F.4 use of E.2 create_review_task(...)."""

    required: bool
    queue_type: str
    target_type: str
    target_id: str
    severity: str
    summary: str
    source_refs: tuple[str, ...]
    blocking_issue_count: int
    metadata: dict[str, Any]


@dataclass(frozen=True, slots=True)
class DocumentValidationResult:
    source_id: str
    validation_report: ValidationReport
    confidence_summary: DocumentConfidenceSummary
    checks: dict[str, Any]
    processed_at: datetime
    validation_version: str
    status: ValidationStatus
    review_recommendation: DocumentReviewRecommendation | None = None

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_safe(item) for item in value]
    return value

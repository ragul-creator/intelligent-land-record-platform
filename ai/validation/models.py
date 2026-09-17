"""Unified validation contracts shared by SIH12 and SIH18."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Iterable


class Severity(StrEnum):
    """Canonical validation severity from the validation/data-quality blueprint."""

    INFO = "INFO"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ValidationStatus(StrEnum):
    VALID = "VALID"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    INVALID = "INVALID"


class ConfidenceBand(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class EvidenceReference:
    """Pointer to evidence without embedding mutable source content."""

    source_type: str
    source_id: str
    page: int | None = None
    region: tuple[float, float, float, float] | None = None
    note: str | None = None


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: Severity
    message: str
    entity_type: str
    entity_id: str
    field_name: str | None = None
    confidence: float | None = None
    review_required: bool = False
    evidence: tuple[EvidenceReference, ...] = ()
    rule_id: str | None = None
    model_version: str | None = None
    processed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if not self.code.strip():
            raise ValueError("validation issue code must not be empty")
        if not self.entity_type.strip() or not self.entity_id.strip():
            raise ValueError("validation issues must identify their entity")


@dataclass(frozen=True)
class ValidationReport:
    status: ValidationStatus
    issues: tuple[ValidationIssue, ...]
    processed_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def review_required(self) -> bool:
        return self.status is ValidationStatus.REVIEW_REQUIRED

    @property
    def has_high_severity_issues(self) -> bool:
        return any(issue.severity is Severity.HIGH for issue in self.issues)


def aggregate_validation(
    issues: Iterable[ValidationIssue],
    *,
    unsafe: bool = False,
) -> ValidationReport:
    """Aggregate issues using canonical routing.

    MEDIUM/HIGH issues require review by default. LOW/INFO may continue unless a
    rule explicitly sets ``review_required``. ``INVALID`` remains reserved for
    data unsafe to continue processing.
    """

    normalized = tuple(issues)
    if unsafe:
        status = ValidationStatus.INVALID
    elif any(
        issue.review_required or issue.severity in {Severity.MEDIUM, Severity.HIGH}
        for issue in normalized
    ):
        status = ValidationStatus.REVIEW_REQUIRED
    else:
        status = ValidationStatus.VALID
    return ValidationReport(status=status, issues=normalized)

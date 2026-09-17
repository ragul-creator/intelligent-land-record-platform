from __future__ import annotations

from ai.validation.confidence.policy import ConfidencePolicy, low_confidence_issue
from ai.validation.models import (
    ConfidenceBand,
    Severity,
    ValidationIssue,
    ValidationStatus,
    aggregate_validation,
)
from ai.validation.rules.common import consistency_issue, format_issue, required_field_issue


def test_default_confidence_bands() -> None:
    policy = ConfidencePolicy()
    assert policy.band(0.95) is ConfidenceBand.HIGH
    assert policy.band(0.90) is ConfidenceBand.HIGH
    assert policy.band(0.80) is ConfidenceBand.MEDIUM
    assert policy.band(0.75) is ConfidenceBand.MEDIUM
    assert policy.band(0.74) is ConfidenceBand.LOW
    assert policy.band(None) is ConfidenceBand.UNKNOWN


def test_low_confidence_forces_review() -> None:
    issue = low_confidence_issue(
        entity_type="DOCUMENT_FIELD",
        entity_id="field-1",
        field_name="survey_number",
        confidence=0.62,
    )
    assert issue is not None
    report = aggregate_validation((issue,))
    assert report.status is ValidationStatus.REVIEW_REQUIRED
    assert report.review_required is True


def test_warning_alone_does_not_force_review() -> None:
    issue = ValidationIssue(
        code="NORMALIZATION_NOTE",
        severity=Severity.WARNING,
        message="Value was normalized.",
        entity_type="DOCUMENT_FIELD",
        entity_id="field-1",
    )
    report = aggregate_validation((issue,))
    assert report.status is ValidationStatus.VALID


def test_unsafe_processing_is_invalid() -> None:
    report = aggregate_validation((), unsafe=True)
    assert report.status is ValidationStatus.INVALID


def test_required_field_rule() -> None:
    issue = required_field_issue(
        entity_type="LAND_RECORD",
        entity_id="record-1",
        field_name="survey_number",
        value="   ",
    )
    assert issue is not None
    assert issue.code == "REQUIRED_FIELD_MISSING"
    assert issue.review_required is True


def test_format_rule() -> None:
    assert format_issue(
        entity_type="LAND_RECORD",
        entity_id="record-1",
        field_name="survey_number",
        value="123/4A",
        pattern=r"[0-9]+/[0-9A-Za-z]+",
    ) is None
    assert format_issue(
        entity_type="LAND_RECORD",
        entity_id="record-1",
        field_name="survey_number",
        value="not-a-survey-number",
        pattern=r"[0-9]+/[0-9A-Za-z]+",
    ) is not None


def test_consistency_rule() -> None:
    assert consistency_issue(
        entity_type="LAND_RECORD",
        entity_id="record-1",
        left_field="district",
        left_value="Chennai",
        right_field="master_district",
        right_value="Chennai",
        message="District mismatch.",
    ) is None
    issue = consistency_issue(
        entity_type="LAND_RECORD",
        entity_id="record-1",
        left_field="district",
        left_value="Chennai",
        right_field="master_district",
        right_value="Madurai",
        message="District mismatch.",
    )
    assert issue is not None
    assert issue.code == "CROSS_FIELD_CONFLICT"

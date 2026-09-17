"""Common deterministic validation helpers for document and GIS entities."""

from __future__ import annotations

import re
from collections.abc import Mapping

from ai.validation.models import EvidenceReference, Severity, ValidationIssue


def required_field_issue(
    *,
    entity_type: str,
    entity_id: str,
    field_name: str,
    value: object,
    evidence: tuple[EvidenceReference, ...] = (),
) -> ValidationIssue | None:
    missing = value is None or (isinstance(value, str) and not value.strip())
    if not missing:
        return None
    return ValidationIssue(
        code="REQUIRED_FIELD_MISSING",
        severity=Severity.ERROR,
        message=f"Required field '{field_name}' is missing.",
        entity_type=entity_type,
        entity_id=entity_id,
        field_name=field_name,
        review_required=True,
        evidence=evidence,
        rule_id=f"REQUIRED.{field_name.upper()}",
    )


def format_issue(
    *,
    entity_type: str,
    entity_id: str,
    field_name: str,
    value: str | None,
    pattern: str,
    evidence: tuple[EvidenceReference, ...] = (),
) -> ValidationIssue | None:
    if value is None or re.fullmatch(pattern, value.strip()):
        return None
    return ValidationIssue(
        code="INVALID_FIELD_FORMAT",
        severity=Severity.ERROR,
        message=f"Field '{field_name}' does not match the configured format.",
        entity_type=entity_type,
        entity_id=entity_id,
        field_name=field_name,
        review_required=True,
        evidence=evidence,
        rule_id=f"FORMAT.{field_name.upper()}",
    )


def consistency_issue(
    *,
    entity_type: str,
    entity_id: str,
    left_field: str,
    left_value: object,
    right_field: str,
    right_value: object,
    message: str,
    evidence: tuple[EvidenceReference, ...] = (),
) -> ValidationIssue | None:
    if left_value == right_value:
        return None
    return ValidationIssue(
        code="CROSS_FIELD_CONFLICT",
        severity=Severity.ERROR,
        message=message,
        entity_type=entity_type,
        entity_id=entity_id,
        review_required=True,
        evidence=evidence,
        rule_id=f"CONSISTENCY.{left_field.upper()}_{right_field.upper()}",
        metadata={
            "left_field": left_field,
            "left_value": left_value,
            "right_field": right_field,
            "right_value": right_value,
        },
    )


def validate_required_fields(
    *,
    entity_type: str,
    entity_id: str,
    values: Mapping[str, object],
    required_fields: tuple[str, ...],
    evidence_by_field: Mapping[str, tuple[EvidenceReference, ...]] | None = None,
) -> tuple[ValidationIssue, ...]:
    evidence_by_field = evidence_by_field or {}
    issues = []
    for field_name in required_fields:
        issue = required_field_issue(
            entity_type=entity_type,
            entity_id=entity_id,
            field_name=field_name,
            value=values.get(field_name),
            evidence=evidence_by_field.get(field_name, ()),
        )
        if issue is not None:
            issues.append(issue)
    return tuple(issues)

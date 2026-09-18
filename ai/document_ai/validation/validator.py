"""Pure F.3 document validation composed from the shared E.1 contracts."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, datetime
from statistics import fmean
from typing import Any

from ai.document_ai.extraction.models import DocumentExtractionResult, ExtractedFieldCandidate
from ai.document_ai.validation.adapter import candidate_evidence, candidate_metadata
from ai.document_ai.validation.models import DocumentConfidenceSummary, DocumentValidationResult, FieldConfidenceSummary
from ai.document_ai.validation.policy import DocumentValidationPolicy
from ai.document_ai.validation.review_routing import recommend_document_review
from ai.validation.confidence.policy import low_confidence_issue
from ai.validation.matching.interfaces import DuplicateCandidate, MasterDataCheck
from ai.validation.models import ConfidenceBand, EvidenceReference, Severity, ValidationIssue, aggregate_validation
from ai.validation.rules.common import format_issue, required_field_issue

ENTITY_TYPE = "LAND_RECORD"


def validate_document_extraction(
    extraction: DocumentExtractionResult,
    policy: DocumentValidationPolicy | None = None,
) -> DocumentValidationResult:
    """Validate preliminary F.2 candidates without persistence or automatic correction."""

    active_policy = policy or DocumentValidationPolicy()
    issues: list[ValidationIssue] = []
    checks: dict[str, Any] = {
        "duplicate_detection": {"status": "NOT_PERFORMED"},
        "master_data_verification": {"status": "NOT_PERFORMED"},
    }

    issues.extend(_required_field_issues(extraction, active_policy))
    issues.extend(_confidence_issues(extraction, active_policy))
    issues.extend(_format_issues(extraction, active_policy))
    issues.extend(_area_issues(extraction))
    conflict_issues = _conflict_issues(extraction)
    issues.extend(conflict_issues)
    duplicate_issues, duplicate_check = _duplicate_issues(extraction, active_policy)
    issues.extend(duplicate_issues)
    checks["duplicate_detection"] = duplicate_check
    verification_issues, verification_check = _master_data_issues(extraction, active_policy)
    issues.extend(verification_issues)
    checks["master_data_verification"] = verification_check

    conflict_fields = {issue.field_name for issue in conflict_issues if issue.field_name is not None}
    confidence_summary = _confidence_summary(extraction, active_policy, conflict_fields)
    report = aggregate_validation(tuple(issues))
    preliminary = DocumentValidationResult(
        source_id=extraction.source_id,
        validation_report=report,
        confidence_summary=confidence_summary,
        checks=checks,
        processed_at=_utc_now(),
        validation_version=active_policy.policy_version,
        status=report.status,
    )
    recommendation = recommend_document_review(preliminary, active_policy)
    return replace(preliminary, review_recommendation=recommendation)


def _required_field_issues(extraction: DocumentExtractionResult, policy: DocumentValidationPolicy) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    for field_name in policy.required_fields:
        candidates = _usable_candidates(extraction.fields.get(field_name, ()))
        issue = required_field_issue(
            entity_type=ENTITY_TYPE,
            entity_id=extraction.source_id,
            field_name=field_name,
            value=candidates[0].original_value if candidates else None,
            evidence=tuple(candidate_evidence(candidate) for candidate in candidates),
        )
        if issue is not None:
            issues.append(issue)
    return tuple(issues)


def _confidence_issues(extraction: DocumentExtractionResult, policy: DocumentValidationPolicy) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    for field_name, candidates in extraction.fields.items():
        for index, candidate in enumerate(_usable_candidates(candidates)):
            evidence = (candidate_evidence(candidate),)
            metadata = candidate_metadata(candidate, candidate_index=index)
            if candidate.confidence is None:
                if policy.unknown_confidence_requires_review:
                    issues.append(
                        ValidationIssue(
                            code="UNKNOWN_CONFIDENCE",
                            severity=Severity.MEDIUM,
                            message="Extraction confidence is unavailable and requires review under the active policy.",
                            entity_type=ENTITY_TYPE,
                            entity_id=extraction.source_id,
                            field_name=field_name,
                            review_required=True,
                            evidence=evidence,
                            rule_id="CONFIDENCE.UNKNOWN",
                            model_version=candidate.extractor_version,
                            metadata={**metadata, "band": ConfidenceBand.UNKNOWN.value},
                        )
                    )
                continue
            issue = low_confidence_issue(
                entity_type=ENTITY_TYPE,
                entity_id=extraction.source_id,
                field_name=field_name,
                confidence=candidate.confidence,
                evidence=evidence,
                model_version=candidate.extractor_version,
                policy=policy.confidence_policy,
            )
            if issue is not None:
                issues.append(replace(issue, metadata={**issue.metadata, **metadata}))
    return tuple(issues)


def _format_issues(extraction: DocumentExtractionResult, policy: DocumentValidationPolicy) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    for field_name, pattern in policy.identifier_patterns.items():
        for index, candidate in enumerate(_usable_candidates(extraction.fields.get(field_name, ()))):
            value = candidate.normalized_value if isinstance(candidate.normalized_value, str) else None
            issue = format_issue(
                entity_type=ENTITY_TYPE,
                entity_id=extraction.source_id,
                field_name=field_name,
                value=value,
                pattern=pattern,
                evidence=(candidate_evidence(candidate),),
            )
            if issue is not None:
                issues.append(
                    replace(
                        issue,
                        model_version=candidate.extractor_version,
                        metadata=candidate_metadata(candidate, candidate_index=index),
                    )
                )
    return tuple(issues)


def _area_issues(extraction: DocumentExtractionResult) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    supported_units = {"sq_ft", "sq_m", "acre", "hectare"}
    for index, candidate in enumerate(_usable_candidates(extraction.fields.get("plot_area", ()))):
        normalized = candidate.normalized_value
        valid = isinstance(normalized, dict) and isinstance(normalized.get("value"), (int, float)) and not isinstance(normalized.get("value"), bool)
        valid = valid and normalized["value"] > 0 and normalized.get("unit") in supported_units
        if not valid:
            issues.append(
                ValidationIssue(
                    code="INVALID_PLOT_AREA",
                    severity=Severity.MEDIUM,
                    message="Plot area must have a positive numeric value and a supported canonical unit.",
                    entity_type=ENTITY_TYPE,
                    entity_id=extraction.source_id,
                    field_name="plot_area",
                    confidence=candidate.confidence,
                    review_required=True,
                    evidence=(candidate_evidence(candidate),),
                    rule_id="AREA.POSITIVE_SUPPORTED_UNIT",
                    model_version=candidate.extractor_version,
                    metadata=candidate_metadata(candidate, candidate_index=index),
                )
            )
    return tuple(issues)


def _conflict_issues(extraction: DocumentExtractionResult) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    for field_name, candidates in extraction.fields.items():
        usable = _usable_candidates(candidates)
        values = {_semantic_value(candidate) for candidate in usable}
        if len(values) <= 1:
            continue
        issues.append(
            ValidationIssue(
                code="FIELD_VALUE_CONFLICT",
                severity=Severity.MEDIUM,
                message=f"Multiple materially different candidates were extracted for '{field_name}'.",
                entity_type=ENTITY_TYPE,
                entity_id=extraction.source_id,
                field_name=field_name,
                review_required=True,
                evidence=tuple(candidate_evidence(candidate) for candidate in usable),
                rule_id=f"CONFLICT.{field_name.upper()}",
                metadata={
                    "candidates": [candidate_metadata(candidate, candidate_index=index) for index, candidate in enumerate(usable)],
                    "distinct_value_count": len(values),
                },
            )
        )
    return tuple(issues)


def _duplicate_issues(
    extraction: DocumentExtractionResult,
    policy: DocumentValidationPolicy,
) -> tuple[tuple[ValidationIssue, ...], dict[str, Any]]:
    if policy.duplicate_detector is None:
        return (), {"status": "NOT_PERFORMED"}
    values = {
        field_name: tuple(candidate.normalized_value for candidate in _usable_candidates(candidates))
        for field_name, candidates in extraction.fields.items()
    }
    matches = tuple(
        match for match in policy.duplicate_detector.find_candidates(entity_type=ENTITY_TYPE, entity_id=extraction.source_id, values=values)
        if match.score >= policy.duplicate_score_threshold
    )
    check = {
        "status": "PERFORMED",
        "threshold": policy.duplicate_score_threshold,
        "candidates": [_duplicate_metadata(match) for match in matches],
    }
    if not matches:
        return (), check
    evidence = tuple(candidate_evidence(candidate) for candidates in extraction.fields.values() for candidate in _usable_candidates(candidates))
    issue = ValidationIssue(
        code="POTENTIAL_DUPLICATE",
        severity=Severity.MEDIUM,
        message="Configured duplicate detector found significant potential duplicate candidates.",
        entity_type=ENTITY_TYPE,
        entity_id=extraction.source_id,
        review_required=True,
        evidence=evidence,
        rule_id="DUPLICATE.DETECTOR",
        metadata={"candidates": [_duplicate_metadata(match) for match in matches]},
    )
    return (issue,), check


def _master_data_issues(
    extraction: DocumentExtractionResult,
    policy: DocumentValidationPolicy,
) -> tuple[tuple[ValidationIssue, ...], dict[str, Any]]:
    if policy.master_data_verifier is None or not policy.verification_fields:
        return (), {"status": "NOT_PERFORMED"}
    checks: list[dict[str, Any]] = []
    issues: list[ValidationIssue] = []
    for field_name in policy.verification_fields:
        for index, candidate in enumerate(_usable_candidates(extraction.fields.get(field_name, ()))):
            check = policy.master_data_verifier.verify(
                field_name=field_name,
                value=candidate.normalized_value,
                context={"source_id": extraction.source_id, "original_value": candidate.original_value},
            )
            check_metadata = _master_check_metadata(field_name, check)
            checks.append(check_metadata)
            if not check.valid:
                issues.append(
                    ValidationIssue(
                        code="MASTER_DATA_VERIFICATION_FAILED",
                        severity=Severity.MEDIUM,
                        message=check.message or f"Configured master-data verification failed for '{field_name}'.",
                        entity_type=ENTITY_TYPE,
                        entity_id=extraction.source_id,
                        field_name=field_name,
                        confidence=candidate.confidence,
                        review_required=True,
                        evidence=(candidate_evidence(candidate),),
                        rule_id=f"MASTER_DATA.{field_name.upper()}",
                        model_version=candidate.extractor_version,
                        metadata={**candidate_metadata(candidate, candidate_index=index), **check_metadata},
                    )
                )
    return tuple(issues), {"status": "PERFORMED", "checks": checks}


def _confidence_summary(
    extraction: DocumentExtractionResult,
    policy: DocumentValidationPolicy,
    conflict_fields: set[str],
) -> DocumentConfidenceSummary:
    fields: list[FieldConfidenceSummary] = []
    representatives: list[float] = []
    unknown_fields = 0
    missing_fields = 0
    for field_name, candidates in extraction.fields.items():
        usable = _usable_candidates(candidates)
        scores = [candidate.confidence for candidate in usable if candidate.confidence is not None]
        unknown_count = len(usable) - len(scores)
        representative = min(scores) if scores else None
        band = ConfidenceBand.UNKNOWN if unknown_count or representative is None else policy.confidence_policy.band(representative)
        fields.append(FieldConfidenceSummary(field_name, representative, band, len(usable), unknown_count))
        if not usable:
            missing_fields += 1
        if representative is not None:
            representatives.append(representative)
        if band is ConfidenceBand.UNKNOWN and usable:
            unknown_fields += 1

    required_missing = any(not _usable_candidates(extraction.fields.get(field_name, ())) for field_name in policy.required_fields)
    document_value = None
    if representatives and not unknown_fields and not required_missing:
        document_value = min(representatives)
    document_band = policy.confidence_policy.band(document_value)
    return DocumentConfidenceSummary(
        value=document_value,
        band=document_band,
        fields=tuple(fields),
        contributing_field_count=len(representatives),
        missing_field_count=missing_fields,
        unknown_confidence_field_count=unknown_fields,
        conflict_field_count=len(conflict_fields),
        policy_version=policy.policy_version,
        high_threshold=policy.confidence_policy.high_threshold,
        medium_threshold=policy.confidence_policy.medium_threshold,
    )


def _usable_candidates(candidates: Iterable[ExtractedFieldCandidate]) -> tuple[ExtractedFieldCandidate, ...]:
    return tuple(candidate for candidate in candidates if candidate.original_value.strip())


def _semantic_value(candidate: ExtractedFieldCandidate) -> str:
    value = candidate.normalized_value if candidate.normalized_value is not None else candidate.original_value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _duplicate_metadata(candidate: DuplicateCandidate) -> dict[str, Any]:
    return {"candidate_id": candidate.candidate_id, "score": candidate.score, "reasons": list(candidate.reasons)}


def _master_check_metadata(field_name: str, check: MasterDataCheck) -> dict[str, Any]:
    return {
        "field_name": field_name,
        "valid": check.valid,
        "source_name": check.source_name,
        "source_reference": check.source_reference,
        "verified_normalized_value": check.normalized_value,
    }


def _utc_now() -> datetime:
    return datetime.now(UTC)

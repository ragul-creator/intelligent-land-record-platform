"""Pure E.1-to-E.2-compatible document review recommendation mapping."""

from __future__ import annotations

from ai.document_ai.validation.models import DocumentReviewRecommendation, DocumentValidationResult
from ai.document_ai.validation.policy import DocumentValidationPolicy, IDENTIFIER_FIELDS
from ai.validation.models import EvidenceReference, Severity, ValidationIssue


def recommend_document_review(
    result: DocumentValidationResult,
    policy: DocumentValidationPolicy,
) -> DocumentReviewRecommendation:
    """Create a non-persisted DOCUMENT review recommendation for a later F.4 adapter."""

    issues = result.validation_report.issues
    if not result.validation_report.review_required:
        return DocumentReviewRecommendation(
            required=False,
            queue_type="DOCUMENT",
            target_type="LAND_RECORD",
            target_id=result.source_id,
            severity="INFO",
            summary="Document extraction validation found no review-required issues.",
            source_refs=(),
            blocking_issue_count=0,
            metadata={"issue_codes": [], "confidence_summary": result.confidence_summary},
        )

    blocking = tuple(issue for issue in issues if _is_blocking(issue, policy))
    severity = _review_severity(issues)
    issue_codes = [issue.code for issue in issues]
    return DocumentReviewRecommendation(
        required=True,
        queue_type="DOCUMENT",
        target_type="LAND_RECORD",
        target_id=result.source_id,
        severity=severity,
        summary="Document extraction review required: " + ", ".join(issue_codes),
        source_refs=tuple(sorted({_source_ref(evidence) for issue in issues for evidence in issue.evidence})),
        blocking_issue_count=len(blocking),
        metadata={
            "issue_codes": issue_codes,
            "blocking_issue_codes": [issue.code for issue in blocking],
            "confidence_summary": result.confidence_summary,
        },
    )


def _review_severity(issues: tuple[ValidationIssue, ...]) -> str:
    highest = max((issue.severity for issue in issues), key=lambda severity: _severity_rank(severity), default=Severity.INFO)
    return {
        Severity.INFO: "INFO",
        Severity.LOW: "LOW",
        Severity.MEDIUM: "MEDIUM",
        Severity.HIGH: "HIGH",
    }[highest]


def _severity_rank(severity: Severity) -> int:
    return {Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2, Severity.HIGH: 3}[severity]


def _is_blocking(issue: ValidationIssue, policy: DocumentValidationPolicy) -> bool:
    if issue.code == "REQUIRED_FIELD_MISSING":
        return True
    if issue.code in {"FIELD_VALUE_CONFLICT", "INVALID_FIELD_FORMAT"} and issue.field_name in IDENTIFIER_FIELDS:
        return True
    if issue.code == "INVALID_PLOT_AREA" and issue.field_name in policy.required_fields:
        return True
    if issue.code == "MASTER_DATA_VERIFICATION_FAILED" and issue.field_name in policy.blocking_master_fields:
        return True
    return issue.code == "LOW_CONFIDENCE" and policy.low_confidence_blocking


def _source_ref(evidence: EvidenceReference) -> str:
    suffix = f":page:{evidence.page}" if evidence.page is not None else ""
    return f"{evidence.source_type}:{evidence.source_id}{suffix}"

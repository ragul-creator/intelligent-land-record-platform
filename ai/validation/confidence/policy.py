"""Configurable confidence policy for Phase E.1.

The default 0.90/0.75 thresholds are hackathon/MVP defaults, not statutory
confidence thresholds. Callers may provide a different policy later.
"""

from __future__ import annotations

from dataclasses import dataclass

from ai.validation.models import ConfidenceBand, EvidenceReference, Severity, ValidationIssue


@dataclass(frozen=True)
class ConfidencePolicy:
    high_threshold: float = 0.90
    medium_threshold: float = 0.75

    def __post_init__(self) -> None:
        if not 0.0 <= self.medium_threshold <= self.high_threshold <= 1.0:
            raise ValueError("confidence thresholds must satisfy 0 <= medium <= high <= 1")

    def band(self, confidence: float | None) -> ConfidenceBand:
        if confidence is None:
            return ConfidenceBand.UNKNOWN
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if confidence >= self.high_threshold:
            return ConfidenceBand.HIGH
        if confidence >= self.medium_threshold:
            return ConfidenceBand.MEDIUM
        return ConfidenceBand.LOW

    def requires_review(self, confidence: float | None) -> bool:
        return self.band(confidence) is ConfidenceBand.LOW


DEFAULT_CONFIDENCE_POLICY = ConfidencePolicy()


def low_confidence_issue(
    *,
    entity_type: str,
    entity_id: str,
    confidence: float,
    field_name: str | None = None,
    evidence: tuple[EvidenceReference, ...] = (),
    model_version: str | None = None,
    policy: ConfidencePolicy = DEFAULT_CONFIDENCE_POLICY,
) -> ValidationIssue | None:
    """Return a review issue when confidence is below the configured medium band."""

    if not policy.requires_review(confidence):
        return None
    return ValidationIssue(
        code="LOW_CONFIDENCE",
        severity=Severity.ERROR,
        message=f"Confidence {confidence:.3f} is below the review threshold {policy.medium_threshold:.3f}.",
        entity_type=entity_type,
        entity_id=entity_id,
        field_name=field_name,
        confidence=confidence,
        review_required=True,
        evidence=evidence,
        rule_id="CONFIDENCE.MINIMUM",
        model_version=model_version,
        metadata={"threshold": policy.medium_threshold, "band": ConfidenceBand.LOW.value},
    )

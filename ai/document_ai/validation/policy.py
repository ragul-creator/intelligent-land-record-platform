"""Configurable MVP document-validation policy; it is not a statutory rule set."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from ai.validation.confidence.policy import ConfidencePolicy, DEFAULT_CONFIDENCE_POLICY
from ai.validation.matching.interfaces import DuplicateDetector, MasterDataVerifier

IDENTIFIER_FIELDS = frozenset({"survey_number", "khasra_number", "khata_number"})
DEFAULT_IDENTIFIER_PATTERNS = {
    field_name: r"(?=.*\d)[A-Za-z0-9]+(?:[/-][A-Za-z0-9]+)*"
    for field_name in IDENTIFIER_FIELDS
}


@dataclass(frozen=True, slots=True)
class DocumentValidationPolicy:
    """Caller-supplied validation configuration, never a universal legal mandate."""

    required_fields: tuple[str, ...] = ()
    confidence_policy: ConfidencePolicy = DEFAULT_CONFIDENCE_POLICY
    unknown_confidence_requires_review: bool = True
    identifier_patterns: Mapping[str, str] = field(default_factory=lambda: dict(DEFAULT_IDENTIFIER_PATTERNS))
    duplicate_detector: DuplicateDetector | None = None
    duplicate_score_threshold: float = 0.90
    master_data_verifier: MasterDataVerifier | None = None
    verification_fields: tuple[str, ...] = ()
    blocking_master_fields: tuple[str, ...] = ()
    low_confidence_blocking: bool = False
    policy_version: str = "document-validation-mvp-v1"

    def __post_init__(self) -> None:
        if not 0.0 <= self.duplicate_score_threshold <= 1.0:
            raise ValueError("duplicate_score_threshold must be between 0 and 1")
        if not self.policy_version.strip():
            raise ValueError("policy_version must not be empty")

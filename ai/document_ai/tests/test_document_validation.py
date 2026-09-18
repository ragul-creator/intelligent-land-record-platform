from __future__ import annotations

import json
from datetime import UTC, datetime

from ai.document_ai.extraction.models import CANONICAL_FIELD_NAMES, DocumentExtractionResult, ExtractedFieldCandidate, FieldEvidence
from ai.document_ai.validation import DocumentValidationPolicy, validate_document_extraction
from ai.validation.confidence.policy import ConfidencePolicy
from ai.validation.matching.interfaces import DuplicateCandidate, MasterDataCheck
from ai.validation.models import ValidationStatus


def _candidate(
    field_name: str,
    original_value: str,
    normalized_value: object,
    confidence: float | None = 0.95,
    *,
    bbox: tuple[int, int, int, int] | None = (10, 20, 30, 40),
) -> ExtractedFieldCandidate:
    from ai.document_ai.models import BoundingBox

    return ExtractedFieldCandidate(
        field_name=field_name,
        original_value=original_value,
        normalized_value=normalized_value,
        confidence=confidence,
        source=FieldEvidence("document-1", 1, BoundingBox(*bbox) if bbox is not None else None),
        model_version="ocr-v1",
        extractor_version="extractor-v1",
        processed_at=datetime.now(UTC),
    )


def _extraction(**field_candidates: tuple[ExtractedFieldCandidate, ...]) -> DocumentExtractionResult:
    fields = {field_name: tuple(field_candidates.get(field_name, ())) for field_name in CANONICAL_FIELD_NAMES}
    return DocumentExtractionResult("document-1", fields, "extractor-v1", datetime.now(UTC))


def _issues(result, code: str):
    return [issue for issue in result.validation_report.issues if issue.code == code]


def test_high_confidence_valid_candidate_has_no_confidence_issue_and_is_valid() -> None:
    result = validate_document_extraction(_extraction(survey_number=(_candidate("survey_number", "123/4", "123/4"),)))

    assert not _issues(result, "LOW_CONFIDENCE")
    assert result.validation_report.status is ValidationStatus.VALID
    assert result.review_recommendation is not None and result.review_recommendation.required is False


def test_low_confidence_and_custom_thresholds_require_review() -> None:
    extraction = _extraction(survey_number=(_candidate("survey_number", "123/4", "123/4", 0.70),))
    default_result = validate_document_extraction(extraction)
    custom_result = validate_document_extraction(
        extraction,
        DocumentValidationPolicy(confidence_policy=ConfidencePolicy(high_threshold=0.95, medium_threshold=0.65)),
    )

    assert _issues(default_result, "LOW_CONFIDENCE")
    assert not _issues(custom_result, "LOW_CONFIDENCE")


def test_unknown_confidence_requires_review_by_default_but_is_configurable() -> None:
    extraction = _extraction(survey_number=(_candidate("survey_number", "123/4", "123/4", None),))

    assert _issues(validate_document_extraction(extraction), "UNKNOWN_CONFIDENCE")
    assert not _issues(validate_document_extraction(extraction, DocumentValidationPolicy(unknown_confidence_requires_review=False)), "UNKNOWN_CONFIDENCE")


def test_required_fields_are_policy_driven_and_missing_values_are_not_invented() -> None:
    extraction = _extraction(district=(_candidate("district", "Chennai", "Chennai"),))
    configured = validate_document_extraction(extraction, DocumentValidationPolicy(required_fields=("survey_number",)))
    unconfigured = validate_document_extraction(extraction)

    assert _issues(configured, "REQUIRED_FIELD_MISSING")
    assert not _issues(unconfigured, "REQUIRED_FIELD_MISSING")


def test_identifier_format_accepts_valid_survey_and_flags_clearly_malformed_value() -> None:
    valid = validate_document_extraction(_extraction(survey_number=(_candidate("survey_number", "123/4", "123/4"),)))
    invalid = validate_document_extraction(_extraction(survey_number=(_candidate("survey_number", "???", "???"),)))

    assert not _issues(valid, "INVALID_FIELD_FORMAT")
    assert _issues(invalid, "INVALID_FIELD_FORMAT")


def test_identical_candidates_agree_but_conflicting_candidates_preserve_all_evidence() -> None:
    identical = validate_document_extraction(
        _extraction(district=(_candidate("district", "Chennai", "Chennai"), _candidate("district", "Chennai", "Chennai")))
    )
    conflict = validate_document_extraction(
        _extraction(survey_number=(_candidate("survey_number", "123/4", "123/4"), _candidate("survey_number", "123/7", "123/7")))
    )

    assert not _issues(identical, "FIELD_VALUE_CONFLICT")
    issue = _issues(conflict, "FIELD_VALUE_CONFLICT")[0]
    assert issue.review_required is True
    assert len(issue.evidence) == 2
    assert len(issue.metadata["candidates"]) == 2


def test_bilingual_text_is_not_translated_or_collapsed() -> None:
    result = validate_document_extraction(
        _extraction(district=(_candidate("district", "சென்னை", "சென்னை"), _candidate("district", "Chennai", "Chennai")))
    )

    issue = _issues(result, "FIELD_VALUE_CONFLICT")[0]
    assert issue.metadata["distinct_value_count"] == 2


def test_valid_and_invalid_plot_areas_are_checked_conservatively() -> None:
    valid = validate_document_extraction(_extraction(plot_area=(_candidate("plot_area", "1200 sq.ft", {"value": 1200, "unit": "sq_ft"}),)))
    invalid = validate_document_extraction(_extraction(plot_area=(_candidate("plot_area", "0 sq.ft", {"value": 0, "unit": "sq_ft"}),)))

    assert not _issues(valid, "INVALID_PLOT_AREA")
    assert _issues(invalid, "INVALID_PLOT_AREA")


def test_evidence_adapter_preserves_bbox_and_leaves_missing_bbox_null() -> None:
    with_bbox = validate_document_extraction(_extraction(survey_number=(_candidate("survey_number", "???", "???"),)))
    without_bbox = validate_document_extraction(_extraction(survey_number=(_candidate("survey_number", "???", "???", bbox=None),)))

    assert _issues(with_bbox, "INVALID_FIELD_FORMAT")[0].evidence[0].region == (10.0, 20.0, 30.0, 40.0)
    assert _issues(without_bbox, "INVALID_FIELD_FORMAT")[0].evidence[0].region is None


def test_duplicate_detector_is_optional_and_preserves_detector_metadata() -> None:
    class Detector:
        def find_candidates(self, **kwargs):
            return [DuplicateCandidate("record-99", 0.96, ("same survey",))]

    extraction = _extraction(survey_number=(_candidate("survey_number", "123/4", "123/4"),))
    absent = validate_document_extraction(extraction)
    present = validate_document_extraction(extraction, DocumentValidationPolicy(duplicate_detector=Detector()))

    assert absent.checks["duplicate_detection"]["status"] == "NOT_PERFORMED"
    issue = _issues(present, "POTENTIAL_DUPLICATE")[0]
    assert issue.metadata["candidates"] == [{"candidate_id": "record-99", "score": 0.96, "reasons": ["same survey"]}]


def test_master_data_verifier_is_optional_and_preserves_success_or_failure_provenance() -> None:
    class Verifier:
        def __init__(self, valid: bool) -> None:
            self.valid = valid

        def verify(self, **kwargs):
            return MasterDataCheck(self.valid, "demo-master", "reference-1", kwargs["value"], "not found" if not self.valid else None)

    extraction = _extraction(survey_number=(_candidate("survey_number", "123/4", "123/4"),))
    absent = validate_document_extraction(extraction)
    success = validate_document_extraction(extraction, DocumentValidationPolicy(master_data_verifier=Verifier(True), verification_fields=("survey_number",)))
    failure = validate_document_extraction(
        extraction,
        DocumentValidationPolicy(master_data_verifier=Verifier(False), verification_fields=("survey_number",), blocking_master_fields=("survey_number",)),
    )

    assert absent.checks["master_data_verification"]["status"] == "NOT_PERFORMED"
    assert success.checks["master_data_verification"]["checks"][0]["source_name"] == "demo-master"
    issue = _issues(failure, "MASTER_DATA_VERIFICATION_FAILED")[0]
    assert issue.evidence[0].source_id == "document-1"
    assert failure.review_recommendation is not None and failure.review_recommendation.blocking_issue_count == 1


def test_confidence_summary_is_conservative_and_unicode_json_round_trips() -> None:
    result = validate_document_extraction(
        _extraction(district=(_candidate("district", "சென்னை", "சென்னை", 0.92),), village=(_candidate("village", "மாதிரி", "மாதிரி", 0.80),))
    )

    assert result.confidence_summary.value == 0.80
    assert result.confidence_summary.contributing_field_count == 2
    recovered = json.loads(json.dumps(result.to_dict(), ensure_ascii=False))
    assert recovered["validation_report"]["issues"] == []
    assert recovered["confidence_summary"]["fields"][0]["field_name"] == "survey_number"


def test_review_recommendation_maps_low_and_blocking_issues_deterministically() -> None:
    low = validate_document_extraction(_extraction(survey_number=(_candidate("survey_number", "123/4", "123/4", 0.60),)))
    blocking = validate_document_extraction(
        _extraction(survey_number=(_candidate("survey_number", "???", "???"),)),
        DocumentValidationPolicy(required_fields=("survey_number",)),
    )

    assert low.review_recommendation is not None
    assert low.review_recommendation.queue_type == "DOCUMENT"
    assert low.review_recommendation.severity == "MEDIUM"
    assert low.review_recommendation.blocking_issue_count == 0
    assert blocking.review_recommendation is not None
    assert blocking.review_recommendation.blocking_issue_count == 1

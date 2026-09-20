"""Pure H.2B.3 validation-policy tests."""

from __future__ import annotations

import uuid

from app.services.validation import (
    AreaComparisonInput,
    IdentifierEvidence,
    detect_area_mismatches,
    detect_duplicate_records,
)


def _identifier(document_id: uuid.UUID, value: str) -> IdentifierEvidence:
    return IdentifierEvidence(
        document_id=document_id,
        validation_result_id=uuid.uuid4(),
        field_id=uuid.uuid4(),
        field_name="survey_number",
        value=value,
        normalized_value=value,
        page_number=1,
        source_id=str(document_id),
    )


def test_duplicate_record_check_normalizes_identifiers_without_self_duplication() -> None:
    first, second = uuid.uuid4(), uuid.uuid4()
    evidence = (
        _identifier(first, "123 / 4"),
        _identifier(first, "123/4"),
        _identifier(second, "123/4"),
        _identifier(uuid.uuid4(), "999/1"),
    )

    issues = detect_duplicate_records(evidence)

    assert len(issues) == 1
    assert issues[0].normalized_identifier == "123/4"
    assert set(issues[0].document_ids) == {first, second}
    assert len(issues[0].source_refs) == 2


def test_area_mismatch_check_uses_relative_difference_and_tolerance() -> None:
    within = AreaComparisonInput(
        link_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        validation_result_id=uuid.uuid4(),
        parcel_id=uuid.uuid4(),
        parcel_identifier="123/4",
        document_area_m2=100.0,
        parcel_area_m2=104.0,
        source_refs=(),
    )
    mismatch = AreaComparisonInput(
        link_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        validation_result_id=uuid.uuid4(),
        parcel_id=uuid.uuid4(),
        parcel_identifier="222/7",
        document_area_m2=100.0,
        parcel_area_m2=80.0,
        source_refs=(),
    )

    issues = detect_area_mismatches((within, mismatch), tolerance=0.05)

    assert len(issues) == 1
    assert issues[0].comparison.link_id == mismatch.link_id
    assert issues[0].relative_difference == 0.2
    assert issues[0].tolerance == 0.05


def test_area_mismatch_check_ignores_nonpositive_areas() -> None:
    invalid = AreaComparisonInput(
        link_id=uuid.uuid4(),
        document_id=uuid.uuid4(),
        validation_result_id=uuid.uuid4(),
        parcel_id=uuid.uuid4(),
        parcel_identifier=None,
        document_area_m2=0.0,
        parcel_area_m2=100.0,
        source_refs=(),
    )
    assert detect_area_mismatches((invalid,)) == ()

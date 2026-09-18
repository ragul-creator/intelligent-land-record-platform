"""Pure deterministic coverage for Phase G.1 matching policy."""

from __future__ import annotations

import uuid

from app.services.record_parcel_links import (
    ParcelMatchInput,
    PersistedFieldEvidence,
    match_record_to_parcels,
)


def _field(name: str, value: str, normalized=None) -> PersistedFieldEvidence:
    return PersistedFieldEvidence(
        field_id=uuid.uuid4(),
        field_name=name,
        value=value,
        normalized_value=normalized,
        page_number=1,
        bounding_box={"left": 10, "top": 20, "width": 30, "height": 12},
        source_id="document-fixture",
    )


def _parcel(identifier: str, *, village: str = "Sample Village", district: str = "Chennai", area_m2: float | None = None) -> ParcelMatchInput:
    return ParcelMatchInput(
        parcel_id=uuid.uuid4(),
        external_identifier=identifier,
        source="CADASTRAL_GIS",
        source_reference="parcel-fixture",
        metadata={"village": village, "district": district},
        area_m2=area_m2,
        area_sqft=None,
    )


def test_unambiguous_exact_identifier_with_consistent_admin_context_auto_confirms() -> None:
    evidence = (_field("survey_number", "123 / 4", "123/4"), _field("village", "Sample Village"), _field("district", "Chennai"))
    result = match_record_to_parcels(evidence, (_parcel("123/4"),))
    assert len(result) == 1
    assert result[0].status == "CONFIRMED"
    assert result[0].method == "EXACT_SURVEY_IDENTIFIER"
    assert result[0].confidence >= 0.95
    assert result[0].provenance["document_fields"][0]["page_number"] == 1


def test_multiple_exact_identifiers_require_review_and_never_auto_confirm() -> None:
    evidence = (_field("survey_number", "123/4"), _field("village", "Sample Village"), _field("district", "Chennai"))
    result = match_record_to_parcels(evidence, (_parcel("123/4"), _parcel("123/4")))
    assert len(result) == 2
    assert {candidate.status for candidate in result} == {"REVIEW_REQUIRED"}
    assert all(candidate.review_required for candidate in result)


def test_missing_identifier_does_not_invent_a_parcel_link() -> None:
    evidence = (_field("village", "Sample Village"), _field("district", "Chennai"), _field("plot_area", "1200 sq ft", {"value": 1200, "unit": "sq_ft"}))
    result = match_record_to_parcels(evidence, (_parcel("123/4", area_m2=1200 / 10.7639104167),))
    assert result == ()


def test_conflicting_administrative_context_lowers_exact_identifier_to_review() -> None:
    evidence = (_field("survey_number", "123/4"), _field("village", "Sample Village"), _field("district", "Madurai"))
    result = match_record_to_parcels(evidence, (_parcel("123/4", district="Chennai"),))
    assert len(result) == 1
    assert result[0].status == "REVIEW_REQUIRED"
    assert result[0].confidence < 0.95
    assert result[0].rationale["match_factors"]["conflicting_admin_fields"] == ["district"]


def test_supported_area_units_are_compared_without_using_geometry_degrees() -> None:
    evidence = (
        _field("survey_number", "123/4"),
        _field("village", "Sample Village"),
        _field("plot_area", "1200 sq ft", {"value": 1200, "unit": "sq_ft"}),
    )
    result = match_record_to_parcels(evidence, (_parcel("123/4", area_m2=1200 / 10.7639104167),))
    comparison = result[0].rationale["area_comparison"]
    assert comparison["compatible"] is True
    assert comparison["document_area_m2"] > 0

from __future__ import annotations

import json
from datetime import UTC, datetime

from ai.document_ai.extraction import CANONICAL_FIELD_NAMES, extract_land_record_fields
from ai.document_ai.models import BoundingBox, DocumentOcrResult, OcrPageResult, OcrRegion, PreprocessingMetadata


def _document(*lines: tuple[str, ...], source_id: str = "document-1") -> DocumentOcrResult:
    regions: list[OcrRegion] = []
    for line_index, parts in enumerate(lines):
        left = 10
        top = 10 + line_index * 30
        for part in parts:
            width = max(12, len(part) * 7)
            regions.append(OcrRegion(part, 0.9, BoundingBox(left, top, width, 12)))
            left += width + 5
    processed_at = datetime.now(UTC)
    page = OcrPageResult(
        source_id=source_id,
        page_number=1,
        text="\n".join(" ".join(line) for line in lines),
        confidence=0.9,
        width=1000,
        height=1000,
        requested_languages=("tam", "eng"),
        project_tested_languages=("tam", "eng"),
        preprocessing=PreprocessingMetadata((), None),
        regions=tuple(regions),
        engine="mock-ocr",
        engine_version="1",
        model_version="mock-ocr-v1",
        processed_at=processed_at,
    )
    return DocumentOcrResult(
        source_id=source_id,
        pages=(page,),
        requested_languages=("tam", "eng"),
        project_tested_languages=("tam", "eng"),
        engine="mock-ocr",
        engine_version="1",
        model_version="mock-ocr-v1",
        processed_at=processed_at,
    )


def _field(result, field_name: str, index: int = 0):
    return result.fields[field_name][index]


def test_extracts_english_district_village_survey_owner_and_area() -> None:
    result = extract_land_record_fields(
        _document(
            ("District:", "Chennai"),
            ("Village:", "Sample", "Village"),
            ("Survey No:", "123/4"),
            ("Owner:", "Test", "Person"),
            ("Area:", "1200", "sq.ft"),
        )
    )

    assert _field(result, "district").normalized_value == "Chennai"
    assert _field(result, "village").normalized_value == "Sample Village"
    assert _field(result, "survey_number").normalized_value == "123/4"
    assert _field(result, "owner_details").original_value == "Test Person"
    assert _field(result, "plot_area").normalized_value == {"value": 1200, "unit": "sq_ft"}


def test_extracts_tamil_values_without_translation_or_unicode_loss() -> None:
    result = extract_land_record_fields(
        _document(
            ("மாவட்டம்:", "சென்னை"),
            ("கிராமம்:", "மாதிரி", "கிராமம்"),
            ("சர்வே எண்:", "123/4"),
            ("உரிமையாளர்:", "சோதனை", "நபர்"),
            ("பரப்பளவு:", "1200", "சதுர", "அடி"),
        )
    )

    assert _field(result, "district").original_value == "சென்னை"
    assert _field(result, "village").normalized_value == "மாதிரி கிராமம்"
    assert _field(result, "survey_number").normalized_value == "123/4"
    assert _field(result, "owner_details").original_value == "சோதனை நபர்"
    assert _field(result, "plot_area").normalized_value == {"value": 1200, "unit": "sq_ft"}


def test_missing_and_unmatched_text_do_not_invent_official_identifiers() -> None:
    result = extract_land_record_fields(_document(("OCR TEST DATA / NOT AN OFFICIAL RECORD",), ("Random number", "123/4")))

    assert result.fields["survey_number"] == ()
    assert result.fields["khasra_number"] == ()
    assert set(result.fields) == set(CANONICAL_FIELD_NAMES)


def test_evidence_has_source_page_and_covering_value_bbox() -> None:
    result = extract_land_record_fields(_document(("Owner:", "Test", "Person"), source_id="source-42"))
    candidate = _field(result, "owner_details")

    assert candidate.source.source_id == "source-42"
    assert candidate.source.page_number == 1
    assert candidate.source.bounding_box == BoundingBox(left=57, top=10, width=75, height=12)
    assert candidate.model_version == "mock-ocr-v1"
    assert candidate.confidence is not None and 0.0 <= candidate.confidence <= 1.0


def test_conflicting_duplicates_remain_as_separate_candidates() -> None:
    result = extract_land_record_fields(_document(("Survey No:", "123/4"), ("Survey Number:", "125/9")))

    assert [candidate.normalized_value for candidate in result.fields["survey_number"]] == ["123/4", "125/9"]


def test_label_on_its_own_line_uses_following_grounded_value() -> None:
    result = extract_land_record_fields(_document(("District",), ("Chennai",)))

    candidate = _field(result, "district")
    assert candidate.original_value == "Chennai"
    assert candidate.source.bounding_box == BoundingBox(left=10, top=40, width=49, height=12)


def test_unicode_json_round_trip_preserves_original_and_normalized_values() -> None:
    result = extract_land_record_fields(_document(("மாவட்டம்:", "சென்னை"),))

    serialized = json.dumps(result.to_dict(), ensure_ascii=False)
    recovered = json.loads(serialized)
    candidate = recovered["fields"]["district"][0]
    assert candidate["original_value"] == "சென்னை"
    assert candidate["normalized_value"] == "சென்னை"
    assert "à®" not in serialized


def test_record_information_preserves_repeatable_detail_and_iso_date() -> None:
    result = extract_land_record_fields(_document(("Mutation No:", "M-12", "dated", "2025-01-15"),))

    assert _field(result, "mutation_records").normalized_value == {
        "entries": [{"value": "M-12 dated 2025-01-15", "dates": ["2025-01-15"]}]
    }


def test_smoke_shaped_tamil_split_labels_marks_and_reversed_area_are_extracted() -> None:
    result = extract_land_record_fields(
        _document(
            ("மாவட்டம்\u200c", ":", "சென்னை"),
            ("கிராமம்\u200c:", "மாதிரி", "கிராமம்\u200c"),
            ("சர்வே", "எண்\u200c", ":", "123/4"),
            ("உரிமையாளர்\u200c:", "சோதனை", "நபர்\u200c"),
            ("பரப்பளவு:", "1200", "அடி", "சதுர"),
        )
    )

    assert _field(result, "district").original_value == "சென்னை"
    assert _field(result, "village").original_value == "மாதிரி கிராமம்\u200c"
    assert _field(result, "survey_number").original_value == "123/4"
    assert _field(result, "survey_number").normalized_value == "123/4"
    assert _field(result, "owner_details").original_value == "சோதனை நபர்\u200c"
    assert _field(result, "plot_area").original_value == "1200 அடி சதுர"
    assert _field(result, "plot_area").normalized_value == {"value": 1200, "unit": "sq_ft"}


def test_spatial_sorting_recovers_out_of_order_tamil_regions() -> None:
    document = _document(("placeholder",))
    page = document.pages[0]
    regions = (
        OcrRegion("123/4", 0.95, BoundingBox(250, 8, 40, 12)),
        OcrRegion("எண்\u200c", 0.95, BoundingBox(100, 10, 50, 12)),
        OcrRegion("சர்வே", 0.95, BoundingBox(10, 10, 70, 12)),
        OcrRegion(":", 0.95, BoundingBox(165, 10, 8, 12)),
    )
    reordered_page = OcrPageResult(
        source_id=page.source_id,
        page_number=page.page_number,
        text="சர்வே எண்: 123/4",
        confidence=page.confidence,
        width=page.width,
        height=page.height,
        requested_languages=page.requested_languages,
        project_tested_languages=page.project_tested_languages,
        preprocessing=page.preprocessing,
        regions=regions,
        engine=page.engine,
        engine_version=page.engine_version,
        model_version=page.model_version,
        processed_at=page.processed_at,
    )
    reordered_document = DocumentOcrResult(
        source_id=document.source_id,
        pages=(reordered_page,),
        requested_languages=document.requested_languages,
        project_tested_languages=document.project_tested_languages,
        engine=document.engine,
        engine_version=document.engine_version,
        model_version=document.model_version,
        processed_at=document.processed_at,
    )

    candidate = _field(extract_land_record_fields(reordered_document), "survey_number")
    assert candidate.original_value == "123/4"
    assert candidate.source.bounding_box == BoundingBox(250, 8, 40, 12)


def test_bilingual_candidates_are_retained_without_collapsing() -> None:
    result = extract_land_record_fields(
        _document(("District:", "Chennai"), ("மாவட்டம்\u200c:", "சென்னை"), ("Village:", "Sample Village"), ("கிராமம்:", "மாதிரி கிராமம்"))
    )

    assert [candidate.original_value for candidate in result.fields["district"]] == ["Chennai", "சென்னை"]
    assert [candidate.original_value for candidate in result.fields["village"]] == ["Sample Village", "மாதிரி கிராமம்"]


def test_noisy_footer_does_not_create_an_official_identifier() -> None:
    result = extract_land_record_fields(_document(("Footer note:", "Survey No", "123/4"), ("Unrelated footer text", "123/4")))

    assert result.fields["survey_number"] == ()

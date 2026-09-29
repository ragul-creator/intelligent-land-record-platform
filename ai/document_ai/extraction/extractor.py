"""Evidence-grounded deterministic extraction from the vendor-neutral F.1 contract."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import fmean

from ai.document_ai.extraction.labels import DEMONSTRATED_LABELS, FieldLabel
from ai.document_ai.extraction.models import CANONICAL_FIELD_NAMES, DocumentExtractionResult, ExtractedFieldCandidate, FieldEvidence
from ai.document_ai.extraction.normalizers import normalize_field_value
from ai.document_ai.models import BoundingBox, DocumentOcrResult, OcrPageResult, OcrRegion

EXTRACTOR_VERSION = "land-record-rule-extractor-f2-v4"
_FIXTURE_MARKERS = ("ocr test data", "not an official record")
_ALLOW_LEADING_OCR_NOISE_FIELDS = {
    "certificate_number",
    "certificate_issued_date",
    "unique_document_reference",
    "stamp_duty_amount",
    "stamp_duty_paid_by",
}

_NARRATIVE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "survey_number",
        re.compile(
            r"\bconsisting\s+in\s+Survey\s+(?:No|Number)\.?\s*[:\-]?\s*(?P<value>[A-Za-z0-9]+(?:\s*[/.-]\s*[A-Za-z0-9]+)*)",
            re.IGNORECASE,
        ),
    ),
    (
        "village",
        re.compile(r"\bsituated\s+at\s+(?P<value>[A-Za-z][A-Za-z .'-]{1,60}?)\s+Village\b", re.IGNORECASE),
    ),
    (
        "tehsil",
        re.compile(r"\bVillage\s*,?\s*(?P<value>[A-Za-z][A-Za-z .'-]{1,60}?)\s+(?:Taluk|Tehsil)\b", re.IGNORECASE),
    ),
    (
        "district",
        re.compile(r"\b(?:Taluk|Tehsil)\s*,?\s*(?P<value>[A-Za-z][A-Za-z .'-]{1,60}?)\s+District\b", re.IGNORECASE),
    ),
    (
        "deed_date",
        re.compile(
            r"\bmade\s+on\s+this\s+(?P<value>\d{1,2}(?:st|nd|rd|th)?(?:\s+day)?\s+of\s+[A-Za-z]+\s+\d{4})",
            re.IGNORECASE,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class _Line:
    page: OcrPageResult
    regions: tuple[OcrRegion, ...]
    text: str


class LandRecordExtractor:
    """Conservative label/value extractor for demonstrated Tamil and English records."""

    def __init__(self, *, labels: tuple[FieldLabel, ...] = DEMONSTRATED_LABELS, version: str = EXTRACTOR_VERSION) -> None:
        self._labels = tuple(sorted(labels, key=lambda label: len(label.text), reverse=True))
        self._version = version

    def extract(self, document: DocumentOcrResult) -> DocumentExtractionResult:
        """Extract candidates only from region-grounded OCR evidence; never invent fields."""

        fields: dict[str, list[ExtractedFieldCandidate]] = {field_name: [] for field_name in CANONICAL_FIELD_NAMES}
        for page in document.pages:
            lines = _group_lines(page)
            for index, line in enumerate(lines):
                if _is_fixture_text(line.text):
                    continue
                for label in self._labels:
                    match = _match_inline_label_value(line.text, label)
                    if match:
                        value_regions = _regions_for_span(line, match.start("value"), match.end("value"))
                        value_regions = _trim_known_separator_noise(label.field_name, value_regions)
                        value = _join_original_value(value_regions) if value_regions else match.group("value").strip()
                        candidate = self._candidate(label.field_name, value, line.page, value_regions, same_line=True)
                        if candidate is not None:
                            fields[label.field_name].append(candidate)
                        continue

                    label_end = _find_label_region_end(line, label)
                    if label_end is not None:
                        value_regions = _value_regions_after_label(line, label_end)
                        if value_regions:
                            candidate = self._candidate(
                                label.field_name,
                                _join_original_value(value_regions),
                                line.page,
                                value_regions,
                                same_line=True,
                            )
                            if candidate is not None:
                                fields[label.field_name].append(candidate)
                            continue

                    if _is_label_only(line.text, label):
                        following = _find_nearby_value_line(lines, index, self._labels)
                        if following is not None:
                            candidate = self._candidate(label.field_name, following.text, line.page, following.regions, same_line=False)
                            if candidate is not None:
                                fields[label.field_name].append(candidate)

            self._append_narrative_candidates(lines, fields)

        return DocumentExtractionResult(
            source_id=document.source_id,
            fields={field_name: tuple(_dedupe_candidates(candidates)) for field_name, candidates in fields.items()},
            extraction_version=self._version,
            processed_at=_utc_now(),
        )

    def _append_narrative_candidates(
        self,
        lines: tuple[_Line, ...],
        fields: dict[str, list[ExtractedFieldCandidate]],
    ) -> None:
        """Recover common deed narrative values only when label rules found none."""

        for field_name, pattern in _NARRATIVE_PATTERNS:
            if fields[field_name]:
                continue
            for line in lines:
                if _is_fixture_text(line.text):
                    continue
                match = pattern.search(line.text)
                if match is None:
                    continue
                value_regions = _regions_for_span(line, match.start("value"), match.end("value"))
                candidate = self._candidate(
                    field_name,
                    match.group("value").strip(" ,.;"),
                    line.page,
                    value_regions,
                    same_line=True,
                )
                if candidate is not None:
                    fields[field_name].append(candidate)
                    break

    def _candidate(
        self,
        field_name: str,
        original_value: str,
        page: OcrPageResult,
        value_regions: tuple[OcrRegion, ...],
        *,
        same_line: bool,
    ) -> ExtractedFieldCandidate | None:
        value = original_value.strip()
        if not value or _is_fixture_text(value):
            return None
        if field_name in {"seller", "buyer"} and _comparison_text(value) in {"and", "between", "seller", "buyer"}:
            return None
        normalized = normalize_field_value(field_name, value)
        return ExtractedFieldCandidate(
            field_name=field_name,
            original_value=value,
            normalized_value=normalized,
            confidence=_extraction_confidence(value_regions, same_line=same_line, normalized=normalized is not None),
            source=FieldEvidence(
                source_id=page.source_id,
                page_number=page.page_number,
                bounding_box=_covering_bbox(value_regions),
            ),
            model_version=page.model_version,
            extractor_version=self._version,
            processed_at=_utc_now(),
        )


def extract_land_record_fields(document_ocr_result: DocumentOcrResult) -> DocumentExtractionResult:
    """Public F.2 entry point for later asynchronous orchestration integration."""

    return LandRecordExtractor().extract(document_ocr_result)


def _group_lines(page: OcrPageResult) -> tuple[_Line, ...]:
    regions = tuple(region for region in page.regions if region.text.strip())
    ordered = sorted(regions, key=lambda region: ((region.bounding_box.top if region.bounding_box else 0), (region.bounding_box.left if region.bounding_box else 0)))
    groups: list[list[OcrRegion]] = []
    for region in ordered:
        if region.bounding_box is None:
            groups.append([region])
            continue
        if not groups or groups[-1][0].bounding_box is None:
            groups.append([region])
            continue
        anchor = groups[-1][0].bounding_box
        tolerance = max(4, max(anchor.height, region.bounding_box.height) // 2)
        if abs(region.bounding_box.top - anchor.top) <= tolerance:
            groups[-1].append(region)
        else:
            groups.append([region])
    split_groups: list[list[OcrRegion]] = []
    for group in groups:
        ordered_group = sorted(group, key=lambda region: region.bounding_box.left if region.bounding_box else 0)
        split_groups.extend(_split_wide_row(page, ordered_group))

    return tuple(
        _Line(
            page=page,
            regions=tuple(group),
            text=" ".join(region.text.strip() for region in group),
        )
        for group in split_groups
        if group
    )


def _split_wide_row(page: OcrPageResult, regions: list[OcrRegion]) -> list[list[OcrRegion]]:
    """Split visually aligned text into columns when a large horizontal gutter is present."""

    if len(regions) < 2:
        return [regions]
    heights = [region.bounding_box.height for region in regions if region.bounding_box is not None and region.bounding_box.height > 0]
    typical_height = sorted(heights)[len(heights) // 2] if heights else 12
    gutter_threshold = max(160, int(page.width * 0.13), typical_height * 7)

    result: list[list[OcrRegion]] = []
    current: list[OcrRegion] = []
    previous: OcrRegion | None = None
    for region in regions:
        if previous is not None and previous.bounding_box is not None and region.bounding_box is not None:
            previous_right = previous.bounding_box.left + previous.bounding_box.width
            gap = region.bounding_box.left - previous_right
            if gap > gutter_threshold and current:
                result.append(current)
                current = []
        current.append(region)
        previous = region
    if current:
        result.append(current)
    return result


def _match_inline_label_value(text: str, label: FieldLabel) -> re.Match[str] | None:
    label_pattern = _raw_label_pattern(label.text)
    return re.match(
        rf"^\s*(?P<label>{label_pattern}\.?)[:\-\s\u200b\u200c\u200d\ufeff]+(?P<value>.+?)\s*$",
        text,
        flags=re.IGNORECASE,
    )


def _is_label_only(text: str, label: FieldLabel) -> bool:
    return _comparison_text(text) == _comparison_text(label.text)


def _find_nearby_value_line(
    lines: tuple[_Line, ...],
    label_index: int,
    labels: tuple[FieldLabel, ...],
) -> _Line | None:
    """Find a spatially adjacent value fragment for a label-only OCR line."""

    label_line = lines[label_index]
    label_boxes = [region.bounding_box for region in label_line.regions if region.bounding_box is not None]
    if not label_boxes:
        if label_index + 1 < len(lines):
            candidate = lines[label_index + 1]
            if not _is_fixture_text(candidate.text) and not _line_starts_with_known_label(candidate.text, labels):
                return candidate
        return None

    label_left = min(box.left for box in label_boxes)
    label_right = max(box.left + box.width for box in label_boxes)
    label_top = min(box.top for box in label_boxes)
    label_height = max(box.height for box in label_boxes)
    page_width = max(1, label_line.page.width)
    row_tolerance = max(18, int(label_height * 1.5))
    max_right_gap = int(page_width * 0.35)
    max_down_gap = max(55, label_height * 3)

    ranked: list[tuple[float, _Line]] = []
    for candidate in lines:
        if candidate is label_line or _is_fixture_text(candidate.text) or _line_starts_with_known_label(candidate.text, labels):
            continue
        boxes = [region.bounding_box for region in candidate.regions if region.bounding_box is not None]
        if not boxes:
            continue
        candidate_left = min(box.left for box in boxes)
        candidate_top = min(box.top for box in boxes)
        vertical_delta = candidate_top - label_top

        # Same visual row: prefer the nearest fragment immediately to the right.
        if abs(vertical_delta) <= row_tolerance and candidate_left >= label_right:
            horizontal_gap = candidate_left - label_right
            if horizontal_gap <= max_right_gap:
                ranked.append((horizontal_gap + abs(vertical_delta) * 4, candidate))
                continue

        # Traditional label-on-one-line, value-on-next-line layout.
        if 0 < vertical_delta <= max_down_gap and abs(candidate_left - label_left) <= int(page_width * 0.15):
            ranked.append((vertical_delta * 5 + abs(candidate_left - label_left), candidate))

    return min(ranked, key=lambda item: item[0])[1] if ranked else None


def _line_starts_with_known_label(text: str, labels: tuple[FieldLabel, ...]) -> bool:
    return any(_match_inline_label_value(text, label) is not None or _is_label_only(text, label) for label in labels)


def _regions_for_span(line: _Line, start: int, end: int) -> tuple[OcrRegion, ...]:
    cursor = 0
    selected: list[OcrRegion] = []
    for index, region in enumerate(line.regions):
        if index:
            cursor += 1
        region_start = cursor
        cursor += len(region.text.strip())
        if region_start < end and cursor > start:
            selected.append(region)
    return tuple(selected)


def _find_label_region_end(line: _Line, label: FieldLabel) -> int | None:
    """Find a split label using comparison-only normalized region tokens."""

    label_tokens = _comparison_text(label.text).split()
    if not label_tokens:
        return None
    region_tokens: list[tuple[str, int]] = []
    for index, region in enumerate(line.regions):
        region_tokens.extend((token, index) for token in _comparison_text(region.text).split())
    max_start = 2 if label.field_name in _ALLOW_LEADING_OCR_NOISE_FIELDS else 0
    for start in range(len(region_tokens) - len(label_tokens) + 1):
        if start > max_start:
            break
        if [token for token, _ in region_tokens[start : start + len(label_tokens)]] == label_tokens:
            return region_tokens[start + len(label_tokens) - 1][1]
    return None


def _value_regions_after_label(line: _Line, label_end: int) -> tuple[OcrRegion, ...]:
    return tuple(region for region in line.regions[label_end + 1 :] if _comparison_text(region.text))


def _trim_known_separator_noise(field_name: str, regions: tuple[OcrRegion, ...]) -> tuple[OcrRegion, ...]:
    """Drop only narrow OCR separator artifacts when the following token is a strong identifier signal."""

    if field_name == "certificate_number" and len(regions) >= 2:
        first = regions[0].text.strip()
        second = regions[1].text.strip()
        if len(first) == 1 and first.isdigit() and len(second) >= 6 and any(character in second for character in "-/"):
            return regions[1:]
    if field_name == "consideration_amount" and len(regions) >= 2:
        first = regions[0]
        second = regions[1].text.strip()
        if len(first.text.strip()) <= 2 and (first.confidence or 0.0) < 0.5 and re.fullmatch(r"[\d,.]+", second):
            return regions[1:]
    return regions


def _join_original_value(regions: tuple[OcrRegion, ...]) -> str:
    """Keep original OCR Unicode while removing only label/value separator punctuation."""

    value = " ".join(region.text.strip() for region in regions).strip()
    return value.lstrip(" \t:-–—")


def _raw_label_pattern(label: str) -> str:
    format_marks = r"[\u200b\u200c\u200d\ufeff]*"
    parts: list[str] = []
    for character in label:
        if character.isspace():
            parts.append(r"[\s\u200b\u200c\u200d\ufeff]+")
        else:
            parts.append(re.escape(character) + format_marks)
    return "".join(parts)


def _comparison_text(value: str) -> str:
    """Normalize only comparison text; never use it as a returned OCR value."""

    normalized = unicodedata.normalize("NFC", value)
    without_format_marks = "".join(character for character in normalized if unicodedata.category(character) != "Cf")
    punctuation_spaced = re.sub(r"[:\-–—]", " ", without_format_marks)
    return " ".join(punctuation_spaced.casefold().split())


def _covering_bbox(regions: tuple[OcrRegion, ...]) -> BoundingBox | None:
    boxes = [region.bounding_box for region in regions if region.bounding_box is not None]
    if not boxes:
        return None
    left = min(box.left for box in boxes)
    top = min(box.top for box in boxes)
    right = max(box.left + box.width for box in boxes)
    bottom = max(box.top + box.height for box in boxes)
    return BoundingBox(left=left, top=top, width=right - left, height=bottom - top)


def _extraction_confidence(regions: tuple[OcrRegion, ...], *, same_line: bool, normalized: bool) -> float | None:
    ocr_scores = [region.confidence for region in regions if region.confidence is not None]
    if not ocr_scores:
        return None
    score = 0.75 * fmean(ocr_scores) + 0.15
    score += 0.05 if same_line else 0.03
    score += 0.05 if normalized else 0.0
    return max(0.0, min(1.0, score))


def _is_fixture_text(value: str) -> bool:
    normalized = _comparison_text(value)
    return any(marker in normalized for marker in _FIXTURE_MARKERS)


def _dedupe_candidates(candidates: list[ExtractedFieldCandidate]) -> list[ExtractedFieldCandidate]:
    """Collapse alias-driven duplicate candidates that point to the same OCR evidence."""

    deduped: list[ExtractedFieldCandidate] = []
    seen: set[tuple[object, ...]] = set()
    for candidate in candidates:
        bbox = candidate.source.bounding_box
        bbox_key = None if bbox is None else (bbox.left, bbox.top, bbox.width, bbox.height)
        key = (
            candidate.field_name,
            candidate.original_value.strip().casefold(),
            candidate.source.page_number,
            bbox_key,
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _utc_now() -> datetime:
    return datetime.now(UTC)

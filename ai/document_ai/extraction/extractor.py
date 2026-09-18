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

EXTRACTOR_VERSION = "land-record-rule-extractor-f2-v1"
_FIXTURE_MARKERS = ("ocr test data", "not an official record")


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
                        value = match.group("value").strip()
                        value_regions = _regions_for_span(line, match.start("value"), match.end("value"))
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

                    if _is_label_only(line.text, label) and index + 1 < len(lines):
                        following = lines[index + 1]
                        if not _is_fixture_text(following.text) and not _line_starts_with_known_label(following.text, self._labels):
                            candidate = self._candidate(label.field_name, following.text, line.page, following.regions, same_line=False)
                            if candidate is not None:
                                fields[label.field_name].append(candidate)

        return DocumentExtractionResult(
            source_id=document.source_id,
            fields={field_name: tuple(candidates) for field_name, candidates in fields.items()},
            extraction_version=self._version,
            processed_at=_utc_now(),
        )

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
    return tuple(
        _Line(
            page=page,
            regions=tuple(sorted(group, key=lambda region: region.bounding_box.left if region.bounding_box else 0)),
            text=" ".join(region.text.strip() for region in sorted(group, key=lambda region: region.bounding_box.left if region.bounding_box else 0)),
        )
        for group in groups
    )


def _match_inline_label_value(text: str, label: FieldLabel) -> re.Match[str] | None:
    label_pattern = _raw_label_pattern(label.text)
    return re.match(
        rf"^\s*(?P<label>{label_pattern}\.?)[:\-\s\u200b\u200c\u200d\ufeff]+(?P<value>.+?)\s*$",
        text,
        flags=re.IGNORECASE,
    )


def _is_label_only(text: str, label: FieldLabel) -> bool:
    return _comparison_text(text) == _comparison_text(label.text)


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
    for start in range(len(region_tokens) - len(label_tokens) + 1):
        if start != 0:
            continue
        if [token for token, _ in region_tokens[start : start + len(label_tokens)]] == label_tokens:
            return region_tokens[start + len(label_tokens) - 1][1]
    return None


def _value_regions_after_label(line: _Line, label_end: int) -> tuple[OcrRegion, ...]:
    return tuple(region for region in line.regions[label_end + 1 :] if _comparison_text(region.text))


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


def _utc_now() -> datetime:
    return datetime.now(UTC)

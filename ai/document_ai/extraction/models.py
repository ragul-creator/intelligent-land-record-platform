"""Typed, JSON-safe contracts for preliminary F.2 extraction candidates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

from ai.document_ai.models import BoundingBox

CANONICAL_FIELD_NAMES = (
    "survey_number",
    "khasra_number",
    "khata_number",
    "owner_details",
    "plot_area",
    "village",
    "tehsil",
    "district",
    "land_classification",
    "mutation_records",
    "registration_information",
)

NormalizedValue = str | int | float | dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class FieldEvidence:
    """Traceability back to one F.1 OCR page and its value-bearing regions."""

    source_id: str
    page_number: int
    bounding_box: BoundingBox | None


@dataclass(frozen=True, slots=True)
class ExtractedFieldCandidate:
    """A single preliminary value candidate; duplicates remain separate candidates."""

    field_name: str
    original_value: str
    normalized_value: NormalizedValue
    confidence: float | None
    source: FieldEvidence
    model_version: str | None
    extractor_version: str
    processed_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentExtractionResult:
    """Preliminary field candidates only; F.3 owns validation and review decisions."""

    source_id: str
    fields: dict[str, tuple[ExtractedFieldCandidate, ...]]
    extraction_version: str
    processed_at: datetime
    status: str = "EXTRACTED_PRELIMINARY"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation preserving Unicode source values."""

        return _json_safe(asdict(self))


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value

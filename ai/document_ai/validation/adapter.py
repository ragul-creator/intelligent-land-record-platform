"""Deterministic F.2 candidate to E.1 evidence/provenance adapters."""

from __future__ import annotations

from typing import Any

from ai.document_ai.extraction.models import ExtractedFieldCandidate
from ai.validation.models import EvidenceReference


def candidate_evidence(candidate: ExtractedFieldCandidate) -> EvidenceReference:
    """Map F.2 pixel evidence without inventing a missing bounding box."""

    box = candidate.source.bounding_box
    region = (float(box.left), float(box.top), float(box.width), float(box.height)) if box is not None else None
    return EvidenceReference(
        source_type="DOCUMENT_OCR",
        source_id=candidate.source.source_id,
        page=candidate.source.page_number,
        region=region,
        note=f"field:{candidate.field_name}",
    )


def candidate_metadata(candidate: ExtractedFieldCandidate, *, candidate_index: int) -> dict[str, Any]:
    """Stable, correction-ready metadata without mutating the source candidate."""

    return {
        "candidate_ref": f"{candidate.source.source_id}:{candidate.source.page_number}:{candidate.field_name}:{candidate_index}",
        "original_value": candidate.original_value,
        "normalized_value": candidate.normalized_value,
        "extraction_confidence": candidate.confidence,
        "extractor_version": candidate.extractor_version,
        "ocr_model_version": candidate.model_version,
    }

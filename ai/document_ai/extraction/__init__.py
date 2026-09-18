"""Deterministic, evidence-grounded preliminary land-record extraction."""

from ai.document_ai.extraction.extractor import LandRecordExtractor, extract_land_record_fields
from ai.document_ai.extraction.models import CANONICAL_FIELD_NAMES, DocumentExtractionResult, ExtractedFieldCandidate, FieldEvidence

__all__ = [
    "CANONICAL_FIELD_NAMES",
    "DocumentExtractionResult",
    "ExtractedFieldCandidate",
    "FieldEvidence",
    "LandRecordExtractor",
    "extract_land_record_fields",
]

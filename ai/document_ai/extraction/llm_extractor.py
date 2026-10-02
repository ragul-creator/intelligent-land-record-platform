"""Optional local-LLM fallback with strict OCR evidence grounding."""
from __future__ import annotations

import json
import re
import urllib.request
from datetime import UTC, datetime
from statistics import fmean
from typing import Any

from ai.document_ai.extraction.models import (
    CANONICAL_FIELD_NAMES, DocumentExtractionResult, ExtractedFieldCandidate, FieldEvidence,
)
from ai.document_ai.extraction.normalizers import normalize_field_value
from ai.document_ai.models import BoundingBox, DocumentOcrResult, OcrPageResult, OcrRegion

LLM_EXTRACTOR_VERSION = "ollama-qwen3-evidence-v1"
_DEFAULT_URL = "http://127.0.0.1:11434/api/generate"

_PROMPT = """You extract land-record fields from OCR text. Return JSON only.
Allowed keys: survey_number, khasra_number, khata_number, owner_details, plot_area,
village, tehsil, district, land_classification, mutation_records, registration_information,
seller, buyer, seller_address, buyer_address, deed_type, deed_date, certificate_number,
certificate_issued_date, unique_document_reference, consideration_amount, stamp_duty_amount,
stamp_duty_paid_by, boundary_north, boundary_south, boundary_east, boundary_west, notary, witnesses.
For every field return an object with exactly value and evidence strings.
Evidence MUST be a short verbatim substring copied from the OCR text that directly supports value.
Do not infer, translate, correct, calculate, or invent values. Omit uncertain fields.
Extract seller and buyer separately when the deed identifies them. Extract each property boundary separately.
For plot_area keep the number and unit. Keep currency/amount wording when present.
Use registration_information only for useful registration details that do not fit a more specific key.
OCR TEXT:\n"""


class OllamaEvidenceExtractor:
    """Use a local Ollama model only for candidates grounded back to OCR regions."""

    def __init__(self, model: str = "qwen3:4b", url: str = _DEFAULT_URL, timeout: float = 90.0) -> None:
        self.model, self.url, self.timeout = model, url, timeout
    def extract(self, document: DocumentOcrResult) -> DocumentExtractionResult:
        fields: dict[str, list[ExtractedFieldCandidate]] = {name: [] for name in CANONICAL_FIELD_NAMES}
        for page in document.pages:
            payload = self._generate(page.text)
            for field_name, item in payload.items():
                if field_name not in fields or not isinstance(item, dict):
                    continue
                value, evidence = item.get("value"), item.get("evidence")
                if not isinstance(value, str) or not isinstance(evidence, str) or not value.strip() or not evidence.strip():
                    continue
                regions = _ground_evidence(page, evidence)
                if not regions or not _value_supported(value, evidence):
                    continue
                normalized = normalize_field_value(field_name, value)
                if normalized is None:
                    continue
                scores = [r.confidence for r in regions if r.confidence is not None]
                confidence = min(0.92, 0.80 * fmean(scores) + 0.10) if scores else None
                fields[field_name].append(ExtractedFieldCandidate(
                    field_name=field_name, original_value=value.strip(), normalized_value=normalized,
                    confidence=confidence,
                    source=FieldEvidence(document.source_id, page.page_number, _covering_bbox(regions)),
                    model_version=self.model, extractor_version=LLM_EXTRACTOR_VERSION, processed_at=datetime.now(UTC),
                ))
        return DocumentExtractionResult(document.source_id, {k: tuple(v) for k, v in fields.items()},
                                        LLM_EXTRACTOR_VERSION, datetime.now(UTC))

    def _generate(self, text: str) -> dict[str, Any]:
        body = json.dumps({"model": self.model, "prompt": _PROMPT + text, "stream": False,
                           "think": False, "format": "json", "options": {"temperature": 0}}).encode("utf-8")
        request = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            outer = json.loads(response.read().decode("utf-8"))
        parsed = json.loads(outer.get("response", "{}"))
        return parsed if isinstance(parsed, dict) else {}

def merge_missing_fields(primary: DocumentExtractionResult, fallback: DocumentExtractionResult) -> DocumentExtractionResult:
    """Keep deterministic candidates; use LLM candidates only where rules found nothing."""
    merged = {name: primary.fields.get(name, ()) or fallback.fields.get(name, ()) for name in CANONICAL_FIELD_NAMES}
    return DocumentExtractionResult(primary.source_id, merged,
                                    f"{primary.extraction_version}+{fallback.extraction_version}", datetime.now(UTC))


def _norm(value: str) -> str:
    return " ".join(re.sub(r"[^\w/.-]+", " ", value.casefold(), flags=re.UNICODE).split())


def _value_supported(value: str, evidence: str) -> bool:
    value_norm, evidence_norm = _norm(value), _norm(evidence)
    if not value_norm or not evidence_norm:
        return False
    if value_norm in evidence_norm:
        return True
    value_tokens = [token for token in value_norm.split() if len(token) > 1]
    return bool(value_tokens) and all(token in evidence_norm for token in value_tokens)


def _ground_evidence(page: OcrPageResult, evidence: str) -> tuple[OcrRegion, ...]:
    target = _norm(evidence)
    if not target or target not in _norm(page.text):
        return ()
    regions = tuple(region for region in page.regions if region.text.strip())
    for start in range(len(regions)):
        combined = ""
        selected: list[OcrRegion] = []
        for region in regions[start:min(len(regions), start + 12)]:
            selected.append(region)
            combined = _norm(" ".join(item.text for item in selected))
            if target in combined:
                return tuple(selected)
            if len(combined) > len(target) * 3 + 40:
                break
    return ()


def _covering_bbox(regions: tuple[OcrRegion, ...]) -> BoundingBox | None:
    boxes = [r.bounding_box for r in regions if r.bounding_box is not None]
    if not boxes:
        return None
    left, top = min(b.left for b in boxes), min(b.top for b in boxes)
    right, bottom = max(b.left + b.width for b in boxes), max(b.top + b.height for b in boxes)
    return BoundingBox(left, top, right - left, bottom - top)

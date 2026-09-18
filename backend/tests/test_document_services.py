"""Focused Phase F.4 unit coverage without a live OCR engine or object store."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

from ai.document_ai.extraction import DocumentExtractionResult, ExtractedFieldCandidate, FieldEvidence
from ai.document_ai.models import BoundingBox
from app.models.documents import Document, DocumentExtractedField
from app.services.documents import DocumentWorkflowError, extraction_from_records, queue_document_job


class FakeSession:
    def __init__(self, records): self.records = records
    def scalars(self, _query): return self.records
    def scalar(self, _query): return None


def test_persisted_field_rehydrates_evidence_without_mutating_original_value() -> None:
    document_id, ocr_id = uuid.uuid4(), uuid.uuid4()
    record = DocumentExtractedField(
        id=uuid.uuid4(), document_id=document_id, ocr_result_id=ocr_id, candidate_index=0,
        field_name="survey_number", original_value="123/4", normalized_value_json="123/4",
        confidence=0.9, page_number=1, bounding_box_json={"left": 10, "top": 20, "width": 30, "height": 12},
        source_id=str(document_id), model_version="tesseract", extractor_version="f2", processed_at=datetime.now(UTC),
    )
    ocr = SimpleNamespace(id=ocr_id, payload_json={"source_id": str(document_id)})
    result = extraction_from_records(FakeSession([record]), document_id=document_id, ocr=ocr)

    candidate = result.fields["survey_number"][0]
    assert candidate.original_value == "123/4"
    assert candidate.source.bounding_box == BoundingBox(left=10, top=20, width=30, height=12)


def test_extraction_contract_keeps_preliminary_candidates_evidence_grounded() -> None:
    candidate = ExtractedFieldCandidate(
        field_name="owner_details", original_value="சோதனை நபர்", normalized_value="சோதனை நபர்", confidence=0.7,
        source=FieldEvidence(source_id="document-1", page_number=1, bounding_box=None), model_version="tesseract",
        extractor_version="f2", processed_at=datetime.now(UTC),
    )
    result = DocumentExtractionResult(source_id="document-1", fields={"owner_details": (candidate,)}, extraction_version="f2", processed_at=datetime.now(UTC))
    assert result.fields["owner_details"][0].original_value == "சோதனை நபர்"


def test_reprocess_requires_prior_persisted_ocr(monkeypatch) -> None:
    document = Document(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        file_id=uuid.uuid4(),
        uploaded_by_user_id=uuid.uuid4(),
        status="UPLOADED",
    )

    class SessionWithoutOcr:
        def scalar(self, _query):
            return None

    monkeypatch.setattr("app.services.documents.active_document_job", lambda _session, _document_id: None)
    with __import__("pytest").raises(DocumentWorkflowError, match="persisted OCR result"):
        queue_document_job(
            SessionWithoutOcr(),
            document=document,
            actor_id=uuid.uuid4(),
            languages=["tam", "eng"],
            reprocess=True,
        )

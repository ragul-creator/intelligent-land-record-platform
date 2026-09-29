"""Clear, evidence-preserving spreadsheet exports for Document AI records."""

from __future__ import annotations

import io
import uuid
from typing import Any

import xlsxwriter
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Document,
    DocumentExtractedField,
    DocumentFieldCorrection,
    DocumentOcrResultRecord,
    DocumentValidationResultRecord,
    File,
)


DISCLAIMER = (
    "Workflow evidence only. OCR text, AI-extracted values, and human corrections "
    "are not statutory ownership proof or certified land-record determinations."
)

FIELD_ORDER = (
    "survey_number", "khasra_number", "khata_number", "owner_details",
    "seller", "buyer", "seller_address", "buyer_address", "plot_area",
    "village", "tehsil", "district", "land_classification", "mutation_records",
    "registration_information", "deed_type", "deed_date", "certificate_number",
    "certificate_issued_date", "unique_document_reference",
    "consideration_amount", "stamp_duty_amount", "stamp_duty_paid_by",
    "boundary_north", "boundary_south", "boundary_east", "boundary_west",
    "notary", "witnesses", "parcel_identifier", "cadastral_identifier",
)
def _latest_ocr(session: Session, document_id: uuid.UUID) -> DocumentOcrResultRecord | None:
    return session.scalar(
        select(DocumentOcrResultRecord)
        .where(DocumentOcrResultRecord.document_id == document_id)
        .order_by(DocumentOcrResultRecord.version.desc())
    )


def _latest_validation(
    session: Session, document_id: uuid.UUID
) -> DocumentValidationResultRecord | None:
    return session.scalar(
        select(DocumentValidationResultRecord)
        .where(DocumentValidationResultRecord.document_id == document_id)
        .order_by(DocumentValidationResultRecord.version.desc())
    )


def _latest_correction(
    session: Session, field_id: uuid.UUID
) -> DocumentFieldCorrection | None:
    return session.scalar(
        select(DocumentFieldCorrection)
        .where(DocumentFieldCorrection.extracted_field_id == field_id)
        .order_by(DocumentFieldCorrection.version.desc())
    )


def _humanize(value: str) -> str:
    return value.replace("_", " ").strip().title()


def _confidence_band(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    if value >= 0.90:
        return "HIGH"
    if value >= 0.75:
        return "MEDIUM"
    return "LOW"
def _document_fields(
    session: Session,
    document: Document,
    ocr: DocumentOcrResultRecord | None,
    *,
    permitted: bool,
) -> list[dict[str, Any]]:
    if ocr is None or not permitted:
        return []

    fields = list(
        session.scalars(
            select(DocumentExtractedField)
            .where(
                DocumentExtractedField.document_id == document.id,
                DocumentExtractedField.ocr_result_id == ocr.id,
            )
            .order_by(DocumentExtractedField.candidate_index)
        )
    )
    result: list[dict[str, Any]] = []
    for field in fields:
        correction = _latest_correction(session, field.id)
        result.append(
            {
                "field_name": field.field_name,
                "effective_value": correction.corrected_value if correction else field.original_value,
                "original_value": field.original_value,
                "corrected": correction is not None,
                "correction_reason": correction.reason if correction else "",
                "confidence": field.confidence,
                "page_number": field.page_number,
                "source_id": field.source_id,
                "model_version": field.model_version or "",
                "extractor_version": field.extractor_version,
                "ocr_result_id": str(field.ocr_result_id),
            }
        )
    return result
def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def build_records_xlsx(
    session: Session,
    *,
    project_id: uuid.UUID,
    project_name: str,
    permissions: set[str],
) -> tuple[bytes, dict[str, int]]:
    documents = list(
        session.scalars(
            select(Document)
            .where(Document.project_id == project_id)
            .order_by(Document.created_at, Document.id)
        )
    )

    prepared: list[dict[str, Any]] = []
    observed_fields: set[str] = set()
    evidence_row_count = 0
    ocr_page_count = 0

    for document in documents:
        source = session.get(File, document.file_id)
        ocr = _latest_ocr(session, document.id)
        validation = _latest_validation(session, document.id)
        permitted = "field:read" in permissions or (
            document.status == "VALIDATED" and "record:read" in permissions
        )
        fields = _document_fields(session, document, ocr, permitted=permitted)
        observed_fields.update(item["field_name"] for item in fields)
        evidence_row_count += len(fields)
        if permitted and ocr is not None:
            ocr_page_count += len((ocr.payload_json or {}).get("pages") or [])
        prepared.append(
            {
                "document": document,
                "source": source,
                "ocr": ocr,
                "validation": validation,
                "permitted": permitted,
                "fields": fields,
            }
        )
    ordered_fields = [name for name in FIELD_ORDER if name in observed_fields]
    ordered_fields.extend(sorted(observed_fields - set(ordered_fields)))

    output = io.BytesIO()
    workbook = xlsxwriter.Workbook(
        output,
        {
            "in_memory": True,
            "strings_to_formulas": False,
            "strings_to_urls": False,
        },
    )
    workbook.set_properties(
        {
            "title": "Bhumi-AI Land-record Evidence Export",
            "subject": "Document AI OCR and structured extraction evidence",
            "company": "Bhumi-AI",
            "comments": DISCLAIMER,
        }
    )

    title_fmt = workbook.add_format(
        {"bold": True, "font_size": 16, "font_color": "#FFFFFF", "bg_color": "#155D72"}
    )
    section_fmt = workbook.add_format(
        {"bold": True, "font_size": 11, "font_color": "#155D72", "bg_color": "#EAF3F6"}
    )
    header_fmt = workbook.add_format(
        {
            "bold": True,
            "font_color": "#FFFFFF",
            "bg_color": "#176B87",
            "border": 1,
            "border_color": "#D5E1E6",
            "text_wrap": True,
            "valign": "vcenter",
        }
    )
    body_fmt = workbook.add_format({"valign": "top", "text_wrap": True})
    muted_fmt = workbook.add_format({"font_color": "#617580", "text_wrap": True})
    percent_fmt = workbook.add_format({"num_format": "0.0%", "valign": "top"})
    warning_fmt = workbook.add_format(
        {"bg_color": "#FFF4E8", "font_color": "#8A5424", "text_wrap": True}
    )
    readme = workbook.add_worksheet("Read Me")
    readme.hide_gridlines(2)
    readme.set_column("A:A", 24)
    readme.set_column("B:B", 88)
    readme.merge_range("A1:B1", "Bhumi-AI — Land-record evidence export", title_fmt)
    readme.write("A3", "Project", section_fmt)
    readme.write("B3", project_name, body_fmt)
    readme.write("A4", "Project ID", section_fmt)
    readme.write("B4", str(project_id), body_fmt)
    readme.write("A6", "Important", section_fmt)
    readme.write("B6", DISCLAIMER, warning_fmt)
    readme.write("A8", "Record Summary", section_fmt)
    readme.write(
        "B8",
        "One row per document. Shows current effective field values. Human corrections, "
        "when present, replace the displayed value without deleting original evidence.",
        body_fmt,
    )
    readme.write("A9", "Extracted Fields", section_fmt)
    readme.write(
        "B9",
        "One row per structured extraction candidate with original value, effective value, "
        "confidence, page, model/extractor provenance, and correction information.",
        body_fmt,
    )
    readme.write("A10", "OCR Text", section_fmt)
    readme.write(
        "B10",
        "Raw OCR page text for documents whose field evidence is visible to the exporting user.",
        body_fmt,
    )
    summary = workbook.add_worksheet("Record Summary")
    summary.hide_gridlines(2)
    base_headers = [
        "Filename", "Workflow Status", "Validation Status", "OCR Confidence",
        "OCR Engine", "OCR Pages",
    ]
    field_headers = [_humanize(name) for name in ordered_fields]
    tail_headers = ["Document ID", "Evidence Note"]
    headers = base_headers + field_headers + tail_headers
    for col, header in enumerate(headers):
        summary.write(0, col, header, header_fmt)
    summary.freeze_panes(1, 1)
    summary.autofilter(0, 0, max(len(prepared), 1), len(headers) - 1)

    for row_index, item in enumerate(prepared, start=1):
        document = item["document"]
        source = item["source"]
        ocr = item["ocr"]
        validation = item["validation"]
        values = {field["field_name"]: field["effective_value"] for field in item["fields"]}
        row_values: list[Any] = [
            source.original_name if source else "",
            document.status,
            validation.status if validation else "",
            ocr.confidence if ocr else None,
            ocr.engine if ocr else "",
            ocr.page_count if ocr else 0,
        ]
        row_values.extend(values.get(name, "") for name in ordered_fields)
        row_values.extend([str(document.id), DISCLAIMER])
        for col, value in enumerate(row_values):
            if col == 3 and isinstance(value, (int, float)):
                summary.write_number(row_index, col, value, percent_fmt)
            else:
                summary.write(row_index, col, _safe_text(value), body_fmt)

    summary.set_column(0, 0, 34)
    summary.set_column(1, 2, 18)
    summary.set_column(3, 3, 15)
    summary.set_column(4, 5, 14)
    if ordered_fields:
        summary.set_column(6, 6 + len(ordered_fields) - 1, 24)
    summary.set_column(len(headers) - 2, len(headers) - 2, 38)
    summary.set_column(len(headers) - 1, len(headers) - 1, 64)
    summary.set_row(0, 34)
    evidence = workbook.add_worksheet("Extracted Fields")
    evidence.hide_gridlines(2)
    evidence_headers = [
        "Filename", "Workflow Status", "Validation Status", "Field Name",
        "Effective Value", "Original Extracted Value", "Corrected",
        "Correction Reason", "Confidence", "Confidence Band", "Page",
        "Source ID", "Model Version", "Extractor Version", "Document ID", "OCR Result ID",
    ]
    for col, header in enumerate(evidence_headers):
        evidence.write(0, col, header, header_fmt)
    evidence.freeze_panes(1, 4)

    evidence_row = 1
    for item in prepared:
        document = item["document"]
        source = item["source"]
        validation = item["validation"]
        for field in item["fields"]:
            values = [
                source.original_name if source else "",
                document.status,
                validation.status if validation else "",
                _humanize(field["field_name"]),
                field["effective_value"],
                field["original_value"],
                "YES" if field["corrected"] else "NO",
                field["correction_reason"],
                field["confidence"],
                _confidence_band(field["confidence"]),
                field["page_number"],
                field["source_id"],
                field["model_version"],
                field["extractor_version"],
                str(document.id),
                field["ocr_result_id"],
            ]
            for col, value in enumerate(values):
                if col == 8 and isinstance(value, (int, float)):
                    evidence.write_number(evidence_row, col, value, percent_fmt)
                else:
                    evidence.write(evidence_row, col, _safe_text(value), body_fmt)
            evidence_row += 1

    if evidence_row > 1:
        evidence.autofilter(0, 0, evidence_row - 1, len(evidence_headers) - 1)
    evidence.set_column(0, 0, 34)
    evidence.set_column(1, 3, 20)
    evidence.set_column(4, 5, 48)
    evidence.set_column(6, 7, 20)
    evidence.set_column(8, 10, 15)
    evidence.set_column(11, 15, 34)
    evidence.set_row(0, 34)
    ocr_sheet = workbook.add_worksheet("OCR Text")
    ocr_sheet.hide_gridlines(2)
    ocr_headers = [
        "Filename", "Page", "OCR Text", "Page Width", "Page Height",
        "OCR Engine", "OCR Confidence", "Document ID", "OCR Result ID",
    ]
    for col, header in enumerate(ocr_headers):
        ocr_sheet.write(0, col, header, header_fmt)
    ocr_sheet.freeze_panes(1, 2)

    ocr_row = 1
    for item in prepared:
        if not item["permitted"] or item["ocr"] is None:
            continue
        document = item["document"]
        source = item["source"]
        ocr = item["ocr"]
        for page_index, page in enumerate((ocr.payload_json or {}).get("pages") or [], start=1):
            values = [
                source.original_name if source else "",
                page_index,
                page.get("text", ""),
                page.get("width", ""),
                page.get("height", ""),
                page.get("engine", ocr.engine),
                ocr.confidence,
                str(document.id),
                str(ocr.id),
            ]
            for col, value in enumerate(values):
                if col == 6 and isinstance(value, (int, float)):
                    ocr_sheet.write_number(ocr_row, col, value, percent_fmt)
                else:
                    ocr_sheet.write(ocr_row, col, _safe_text(value), body_fmt)
            ocr_row += 1

    if ocr_row > 1:
        ocr_sheet.autofilter(0, 0, ocr_row - 1, len(ocr_headers) - 1)
    ocr_sheet.set_column(0, 0, 34)
    ocr_sheet.set_column(1, 1, 9)
    ocr_sheet.set_column(2, 2, 90)
    ocr_sheet.set_column(3, 6, 15)
    ocr_sheet.set_column(7, 8, 38)
    ocr_sheet.set_row(0, 34)

    workbook.close()
    return output.getvalue(), {
        "document_count": len(documents),
        "field_count": evidence_row_count,
        "ocr_page_count": ocr_page_count,
    }

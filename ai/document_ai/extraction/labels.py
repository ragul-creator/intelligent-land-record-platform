"""Modular demonstrated label dictionaries for deterministic F.2 extraction."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class FieldLabel:
    field_name: str
    text: str


# These Tamil/English terms support the demonstrated scenario only. Other state or
# language dictionaries can add FieldLabel instances without changing extraction.
DEMONSTRATED_LABELS = (
    FieldLabel("survey_number", "Survey No"),
    FieldLabel("survey_number", "Survey Number"),
    FieldLabel("survey_number", "சர்வே எண்"),
    FieldLabel("khasra_number", "Khasra No"),
    FieldLabel("khasra_number", "Khasra Number"),
    FieldLabel("khasra_number", "கஸ்ரா எண்"),
    FieldLabel("khata_number", "Khata No"),
    FieldLabel("khata_number", "Khata Number"),
    FieldLabel("khata_number", "கத்தா எண்"),
    FieldLabel("owner_details", "Owner Name"),
    FieldLabel("owner_details", "Owner"),
    FieldLabel("owner_details", "உரிமையாளர் பெயர்"),
    FieldLabel("owner_details", "உரிமையாளர்"),
    FieldLabel("plot_area", "Plot Area"),
    FieldLabel("plot_area", "Area"),
    FieldLabel("plot_area", "பரப்பளவு"),
    FieldLabel("village", "Village"),
    FieldLabel("village", "கிராமம்"),
    FieldLabel("tehsil", "Tehsil"),
    FieldLabel("tehsil", "Taluk"),
    FieldLabel("tehsil", "தாலுகா"),
    FieldLabel("tehsil", "வட்டம்"),
    FieldLabel("district", "District"),
    FieldLabel("district", "மாவட்டம்"),
    FieldLabel("land_classification", "Land Classification"),
    FieldLabel("land_classification", "Land Type"),
    FieldLabel("land_classification", "நில வகைப்பாடு"),
    FieldLabel("land_classification", "நில வகை"),
    FieldLabel("mutation_records", "Mutation Details"),
    FieldLabel("mutation_records", "Mutation No"),
    FieldLabel("mutation_records", "Mutation"),
    FieldLabel("mutation_records", "பெயர்மாற்றம்"),
    FieldLabel("registration_information", "Registration Details"),
    FieldLabel("registration_information", "Registration No"),
    FieldLabel("registration_information", "Registration"),
    FieldLabel("registration_information", "பதிவு எண்"),
    # Common Tamil Nadu e-stamp / deed labels. These remain evidence-grounded:
    # values are emitted only when OCR regions contain the label and value.
    FieldLabel("owner_details", "Purchased by"),
    FieldLabel("deed_type", "Description of Document"),
    FieldLabel("consideration_amount", "Consideration Price (Rs.)"),
    FieldLabel("consideration_amount", "Consideration Price"),
    FieldLabel("seller", "First Party"),
    FieldLabel("seller", "Fest Party"),  # conservative OCR alias observed on low-resolution scans
    FieldLabel("buyer", "Second Party"),
    FieldLabel("certificate_number", "Certificate No."),
    FieldLabel("certificate_number", "Certificate No"),
    FieldLabel("certificate_number", "Certificate Number"),
    FieldLabel("certificate_issued_date", "Certificate Issued Date"),
    FieldLabel("certificate_issued_date", "Certificate tssued Date"),  # OCR I/t substitution
    FieldLabel("unique_document_reference", "Unique Doc. Reference"),
    FieldLabel("unique_document_reference", "Unique Doc Reference"),
    FieldLabel("unique_document_reference", "Unique Document Reference"),
    FieldLabel("unique_document_reference", "Doc. Reference"),
    FieldLabel("stamp_duty_paid_by", "Stamp Duty Paid By"),
    FieldLabel("stamp_duty_paid_by", "Stamp Duty Pad By"),  # OCR dropped the i
    FieldLabel("stamp_duty_amount", "Stamp Duty Amount(Rs.)"),
    FieldLabel("stamp_duty_amount", "Stamp Duty Amount"),
    FieldLabel("stamp_duty_amount", "Stamp Duty AmountiRs.}"),  # OCR punctuation substitution
    FieldLabel("plot_area", "Total Extent"),
    FieldLabel("boundary_north", "North"),
    FieldLabel("boundary_south", "South"),
    FieldLabel("boundary_east", "East"),
    FieldLabel("boundary_west", "West"),
)

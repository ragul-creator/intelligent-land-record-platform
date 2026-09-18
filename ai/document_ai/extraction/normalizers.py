"""Conservative deterministic normalizers that never invent source values."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

_IDENTIFIER_SEPARATOR = re.compile(r"\s*([/-])\s*")
_AREA_PATTERN = re.compile(
    r"(?P<value>\d+(?:[,.]\d+)?)\s*(?P<unit>sq\.?\s*ft\.?|sqft|square\s+feet|square\s+foot|m2|m²|square\s+met(?:re|er)s?|acres?|hectares?|சதுர\s+அடி|அடி\s+சதுர)",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"\b(?P<year>\d{4})[-/.](?P<month>\d{1,2})[-/.](?P<day>\d{1,2})\b")


def normalize_whitespace(value: str) -> str:
    """Collapse ordinary whitespace without translating or transliterating text."""

    return " ".join(value.strip().split())


def normalize_identifier(value: str) -> str:
    """Preserve identifier components while removing harmless separator spacing."""

    return _IDENTIFIER_SEPARATOR.sub(r"\1", normalize_whitespace(value))


def normalize_area(value: str) -> dict[str, Any] | None:
    """Return a unit-preserving structured area only when an explicit unit is present."""

    match = _AREA_PATTERN.search(value)
    if not match:
        return None
    numeric = float(match.group("value").replace(",", ""))
    normalized_value: int | float = int(numeric) if numeric.is_integer() else numeric
    unit = re.sub(r"\s+", " ", match.group("unit").casefold()).replace(".", "").strip()
    if unit in {"sq ft", "sqft", "square feet", "square foot", "சதுர அடி", "அடி சதுர"}:
        canonical_unit = "sq_ft"
    elif unit in {"m2", "m²", "square metre", "square metres", "square meter", "square meters"}:
        canonical_unit = "sq_m"
    elif unit in {"acre", "acres"}:
        canonical_unit = "acre"
    else:
        canonical_unit = "hectare"
    return {"value": normalized_value, "unit": canonical_unit}


def normalize_record_information(value: str) -> dict[str, Any]:
    """Preserve repeatable mutation/registration detail and only normalize ISO-like dates."""

    normalized = normalize_whitespace(value)
    dates = tuple(_normalize_iso_date(match) for match in _ISO_DATE.finditer(normalized))
    result: dict[str, Any] = {"entries": [{"value": normalized}]}
    if dates:
        result["entries"][0]["dates"] = list(dates)
    return result


def normalize_field_value(field_name: str, value: str) -> str | dict[str, Any] | None:
    if field_name in {"survey_number", "khasra_number", "khata_number"}:
        return normalize_identifier(value)
    if field_name == "plot_area":
        return normalize_area(value)
    if field_name in {"mutation_records", "registration_information"}:
        return normalize_record_information(value)
    return normalize_whitespace(value)


def _normalize_iso_date(match: re.Match[str]) -> str:
    try:
        return date(int(match.group("year")), int(match.group("month")), int(match.group("day"))).isoformat()
    except ValueError:
        return match.group(0)

"""Typed, vendor-neutral contracts for preliminary OCR output."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class BoundingBox:
    """A pixel bounding box in the rendered source-page coordinate system."""

    left: int
    top: int
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class OcrRegion:
    """A recognized OCR token or line with optional engine-supplied confidence."""

    text: str
    confidence: float | None
    bounding_box: BoundingBox | None
    kind: str = "word"


@dataclass(frozen=True, slots=True)
class PreprocessingMetadata:
    """The non-destructive operations applied to a rendered page derivative."""

    operations: tuple[str, ...]
    deskew_angle_degrees: float | None


@dataclass(frozen=True, slots=True)
class EnginePageResult:
    """Vendor-neutral result returned by an OCR engine before page provenance is added."""

    text: str
    confidence: float | None
    regions: tuple[OcrRegion, ...]
    engine: str
    engine_version: str | None
    model_version: str | None


@dataclass(frozen=True, slots=True)
class OcrPageResult:
    """Preliminary OCR output for one source page; it is not verified record data."""

    source_id: str
    page_number: int
    text: str
    confidence: float | None
    width: int
    height: int
    requested_languages: tuple[str, ...]
    project_tested_languages: tuple[str, ...]
    preprocessing: PreprocessingMetadata
    regions: tuple[OcrRegion, ...]
    engine: str
    engine_version: str | None
    model_version: str | None
    processed_at: datetime


@dataclass(frozen=True, slots=True)
class DocumentOcrResult:
    """A traceable document OCR result suitable for later F.2/F.3 processing."""

    source_id: str
    pages: tuple[OcrPageResult, ...]
    requested_languages: tuple[str, ...]
    project_tested_languages: tuple[str, ...]
    engine: str
    engine_version: str | None
    model_version: str | None
    processed_at: datetime
    status: str = "OCR_PRELIMINARY"

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation without retaining source image bytes."""

        return _json_safe(asdict(self))


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


__all__ = [
    "BoundingBox",
    "DocumentOcrResult",
    "EnginePageResult",
    "OcrPageResult",
    "OcrRegion",
    "PreprocessingMetadata",
]

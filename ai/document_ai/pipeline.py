"""Composable PDF/image preprocessing and preliminary OCR pipeline for Phase F.1."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from ai.document_ai.document_loader import load_document_pages
from ai.document_ai.languages import PROJECT_TESTED_LANGUAGES, validate_language_codes
from ai.document_ai.models import DocumentOcrResult, OcrPageResult
from ai.document_ai.ocr.base import OcrEngine
from ai.document_ai.ocr.tesseract import TesseractOcrEngine
from ai.document_ai.preprocessing.image import PreprocessingConfig, preprocess_image

DEFAULT_REQUESTED_LANGUAGES = PROJECT_TESTED_LANGUAGES


class OcrPipeline:
    """Read an immutable local document into traceable, preliminary OCR page results."""

    def __init__(
        self,
        engine: OcrEngine | None = None,
        *,
        preprocessing: PreprocessingConfig | None = None,
        dpi: int = 300,
    ) -> None:
        self._engine = engine or TesseractOcrEngine()
        self._preprocessing = preprocessing or PreprocessingConfig()
        self._dpi = dpi

    def process(
        self,
        source: str | Path,
        *,
        source_id: str | None = None,
        languages: Sequence[str] = DEFAULT_REQUESTED_LANGUAGES,
        allowed_root: str | Path | None = None,
    ) -> DocumentOcrResult:
        """Return preliminary OCR output; callers retain ownership of source files."""

        source_reference = source_id or str(Path(source).expanduser().resolve())
        requested_languages = validate_language_codes(languages)
        pages: list[OcrPageResult] = []
        for source_page in load_document_pages(source, dpi=self._dpi, allowed_root=allowed_root):
            processed_image, preprocessing = preprocess_image(source_page.image, self._preprocessing)
            recognition = self._engine.recognize(processed_image, languages=requested_languages)
            pages.append(
                OcrPageResult(
                    source_id=source_reference,
                    page_number=source_page.page_number,
                    text=recognition.text,
                    confidence=recognition.confidence,
                    width=processed_image.width,
                    height=processed_image.height,
                    requested_languages=requested_languages,
                    project_tested_languages=PROJECT_TESTED_LANGUAGES,
                    preprocessing=preprocessing,
                    regions=recognition.regions,
                    engine=recognition.engine,
                    engine_version=recognition.engine_version,
                    model_version=recognition.model_version,
                    processed_at=_utc_now(),
                )
            )

        processed_at = _utc_now()
        first_page = pages[0] if pages else None
        return DocumentOcrResult(
            source_id=source_reference,
            pages=tuple(pages),
            requested_languages=requested_languages,
            project_tested_languages=PROJECT_TESTED_LANGUAGES,
            engine=first_page.engine if first_page else getattr(self._engine, "name", "unknown"),
            engine_version=first_page.engine_version if first_page else None,
            model_version=first_page.model_version if first_page else None,
            processed_at=processed_at,
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)

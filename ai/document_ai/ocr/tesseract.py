"""Lazy Tesseract adapter for configurable pretrained printed-text OCR."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from PIL import Image

from ai.document_ai.errors import OcrEngineError, OcrLanguageUnavailableError
from ai.document_ai.languages import validate_language_codes
from ai.document_ai.models import BoundingBox, EnginePageResult, OcrRegion


class TesseractBackend(Protocol):
    """Small adapter surface that keeps vendor structures out of the pipeline."""

    def get_languages(self) -> Sequence[str]: ...

    def get_version(self) -> str: ...

    def image_to_data(self, image: Image.Image, *, language: str, config: str) -> dict[str, list[Any]]: ...

    def image_to_string(self, image: Image.Image, *, language: str, config: str) -> str: ...


class TesseractOcrEngine:
    """Pretrained Tesseract implementation; imports vendor code only when used."""

    name = "tesseract"

    def __init__(self, *, command: str | None = None, backend: TesseractBackend | None = None) -> None:
        self._command = command
        self._backend = backend

    def recognize(self, image: Image.Image, *, languages: Sequence[str]) -> EnginePageResult:
        requested = validate_language_codes(languages)
        backend = self._get_backend()
        available = set(self.available_languages())
        missing = sorted(set(requested) - available)
        if missing:
            raise OcrLanguageUnavailableError(
                "Tesseract language data is missing for: " + ", ".join(missing) + ". Install the matching traineddata files; no English fallback was used."
            )

        configuration = "--oem 3 --psm 3"
        language = "+".join(requested)
        try:
            data = backend.image_to_data(image, language=language, config=configuration)
            text = backend.image_to_string(image, language=language, config=configuration).strip()
            engine_version = backend.get_version()
        except OcrEngineError:
            raise
        except Exception as error:
            raise OcrEngineError(f"Tesseract OCR failed: {error}") from error

        regions = _regions_from_data(data)
        confidences = [region.confidence for region in regions if region.confidence is not None]
        return EnginePageResult(
            text=text,
            confidence=sum(confidences) / len(confidences) if confidences else None,
            regions=tuple(regions),
            engine=self.name,
            engine_version=engine_version,
            model_version=None,
        )

    def available_languages(self) -> tuple[str, ...]:
        """Return installed Tesseract traineddata identifiers without imposing an allowlist."""

        return tuple(sorted(set(self._get_backend().get_languages())))

    def _get_backend(self) -> TesseractBackend:
        if self._backend is not None:
            return self._backend
        try:
            import pytesseract
        except ImportError as error:
            raise OcrEngineError("pytesseract is not installed. Install ai/document_ai/requirements.txt before running OCR.") from error

        if self._command:
            pytesseract.pytesseract.tesseract_cmd = self._command
        self._backend = _PytesseractBackend(pytesseract)
        return self._backend


class _PytesseractBackend:
    def __init__(self, module: Any) -> None:
        self._module = module

    def get_languages(self) -> Sequence[str]:
        try:
            return self._module.get_languages(config="")
        except Exception as error:
            raise OcrEngineError(
                "Tesseract executable is unavailable. Install Tesseract and ensure it is on PATH, or pass its command path explicitly."
            ) from error

    def get_version(self) -> str:
        try:
            return str(self._module.get_tesseract_version())
        except Exception:
            return "unknown"

    def image_to_data(self, image: Image.Image, *, language: str, config: str) -> dict[str, list[Any]]:
        return self._module.image_to_data(image, lang=language, config=config, output_type=self._module.Output.DICT)

    def image_to_string(self, image: Image.Image, *, language: str, config: str) -> str:
        return self._module.image_to_string(image, lang=language, config=config)


def _regions_from_data(data: dict[str, list[Any]]) -> list[OcrRegion]:
    texts = data.get("text", [])
    regions: list[OcrRegion] = []
    for index, raw_text in enumerate(texts):
        text = str(raw_text).strip()
        if not text:
            continue
        confidence = normalize_confidence(_value_at(data, "conf", index))
        left = _int_at(data, "left", index)
        top = _int_at(data, "top", index)
        width = _int_at(data, "width", index)
        height = _int_at(data, "height", index)
        bounding_box = None
        if None not in (left, top, width, height) and width >= 0 and height >= 0:
            bounding_box = BoundingBox(left, top, width, height)
        regions.append(OcrRegion(text=text, confidence=confidence, bounding_box=bounding_box))
    return regions


def normalize_confidence(value: object) -> float | None:
    """Normalize Tesseract's 0-100 confidence to 0.0-1.0; -1 means unavailable."""

    try:
        numeric = float(str(value))
    except (TypeError, ValueError):
        return None
    if numeric < 0:
        return None
    return max(0.0, min(1.0, numeric / 100.0))


def _value_at(data: dict[str, list[Any]], key: str, index: int) -> Any:
    values = data.get(key, [])
    return values[index] if index < len(values) else None


def _int_at(data: dict[str, list[Any]], key: str, index: int) -> int | None:
    value = _value_at(data, key, index)
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

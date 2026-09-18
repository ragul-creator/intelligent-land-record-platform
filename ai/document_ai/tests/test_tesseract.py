from __future__ import annotations

import pytest
from PIL import Image

from ai.document_ai.errors import OcrEngineError, OcrLanguageConfigurationError, OcrLanguageUnavailableError
from ai.document_ai.languages import parse_language_configuration, validate_language_codes
from ai.document_ai.ocr.tesseract import TesseractOcrEngine, normalize_confidence


class FakeBackend:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.last_language: str | None = None

    def get_languages(self) -> list[str]:
        return ["eng", "tam"]

    def get_version(self) -> str:
        return "5.5.0"

    def image_to_data(self, image: Image.Image, *, language: str, config: str) -> dict[str, list[object]]:
        if self.fail:
            raise RuntimeError("binary failure")
        self.last_language = language
        assert config == "--oem 3 --psm 3"
        return {
            "text": ["Land", "", "record"],
            "conf": ["95.5", "-1", "80"],
            "left": [4, 0, 40],
            "top": [5, 0, 5],
            "width": [30, 0, 45],
            "height": [12, 0, 12],
        }

    def image_to_string(self, image: Image.Image, *, language: str, config: str) -> str:
        return "Land record\n"


def test_tesseract_adapter_normalizes_confidence_and_regions() -> None:
    backend = FakeBackend()
    result = TesseractOcrEngine(backend=backend).recognize(Image.new("L", (100, 30)), languages=("tam", "eng"))

    assert backend.last_language == "tam+eng"
    assert result.text == "Land record"
    assert result.confidence == pytest.approx(0.8775)
    assert [region.text for region in result.regions] == ["Land", "record"]
    assert result.regions[0].confidence == pytest.approx(0.955)
    assert result.regions[0].bounding_box.left == 4
    assert result.engine == "tesseract"
    assert result.engine_version == "5.5.0"


def test_tesseract_accepts_another_installed_indian_language_configuration() -> None:
    class HindiBackend(FakeBackend):
        def get_languages(self) -> list[str]:
            return ["eng", "hin"]

    backend = HindiBackend()
    TesseractOcrEngine(backend=backend).recognize(Image.new("L", (100, 30)), languages=("hin", "eng"))

    assert backend.last_language == "hin+eng"


def test_engine_exposes_installed_language_packs() -> None:
    assert TesseractOcrEngine(backend=FakeBackend()).available_languages() == ("eng", "tam")


@pytest.mark.parametrize(
    ("value", "expected"), [("0", 0.0), ("100", 1.0), ("150", 1.0), ("-1", None), ("unknown", None)]
)
def test_confidence_normalization(value: str, expected: float | None) -> None:
    assert normalize_confidence(value) == expected


def test_empty_tesseract_output_is_safe() -> None:
    class EmptyBackend(FakeBackend):
        def image_to_data(self, image: Image.Image, *, language: str, config: str) -> dict[str, list[object]]:
            return {"text": [], "conf": []}

        def image_to_string(self, image: Image.Image, *, language: str, config: str) -> str:
            return "   "

    result = TesseractOcrEngine(backend=EmptyBackend()).recognize(Image.new("L", (20, 20)), languages=("eng",))

    assert result.text == ""
    assert result.confidence is None
    assert result.regions == ()


def test_missing_requested_language_fails_without_fallback() -> None:
    class EnglishOnlyBackend(FakeBackend):
        def get_languages(self) -> list[str]:
            return ["eng"]

    with pytest.raises(OcrLanguageUnavailableError, match="hin"):
        TesseractOcrEngine(backend=EnglishOnlyBackend()).recognize(Image.new("L", (20, 20)), languages=("hin", "eng"))


def test_engine_failure_uses_domain_specific_error() -> None:
    with pytest.raises(OcrEngineError, match="Tesseract OCR failed"):
        TesseractOcrEngine(backend=FakeBackend(fail=True)).recognize(Image.new("L", (20, 20)), languages=("eng",))


@pytest.mark.parametrize("value", ["tam+", "tam++eng", "tam;eng", "TAM+eng"])
def test_malformed_cli_language_configuration_is_rejected(value: str) -> None:
    with pytest.raises(OcrLanguageConfigurationError):
        parse_language_configuration(value)


@pytest.mark.parametrize("languages", [("tam+eng",), ("tam", "eng", "eng")])
def test_malformed_adapter_language_configuration_is_rejected(languages: tuple[str, ...]) -> None:
    with pytest.raises(OcrLanguageConfigurationError):
        validate_language_codes(languages)

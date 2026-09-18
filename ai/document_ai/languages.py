"""Safe, generic configuration of installed Tesseract language identifiers."""

from __future__ import annotations

import re
from collections.abc import Sequence

from ai.document_ai.errors import OcrLanguageConfigurationError

# These are the project demo languages, not an allowlist for the OCR architecture.
PROJECT_TESTED_LANGUAGES = ("tam", "eng")
_LANGUAGE_IDENTIFIER = re.compile(r"[a-z][a-z0-9_]{1,31}")


def parse_language_configuration(value: str) -> tuple[str, ...]:
    """Parse a CLI-style Tesseract language string such as ``hin+eng`` safely."""

    if not isinstance(value, str):
        raise OcrLanguageConfigurationError("OCR language configuration must be a '+'-separated string.")
    return validate_language_codes(tuple(value.split("+")))


def validate_language_codes(languages: Sequence[str]) -> tuple[str, ...]:
    """Validate generic traineddata identifiers without restricting them to known languages."""

    if isinstance(languages, str):
        raise OcrLanguageConfigurationError("OCR languages must be separate identifiers, not one combined string.")
    requested = tuple(languages)
    if not requested:
        raise OcrLanguageConfigurationError("At least one OCR language must be requested.")
    if len(set(requested)) != len(requested):
        raise OcrLanguageConfigurationError("OCR language identifiers must not be duplicated.")
    invalid = [language for language in requested if not isinstance(language, str) or not _LANGUAGE_IDENTIFIER.fullmatch(language)]
    if invalid:
        raise OcrLanguageConfigurationError(
            "Invalid OCR language identifier(s): " + ", ".join(repr(language) for language in invalid) + ". Use lowercase Tesseract traineddata identifiers such as tam, hin, or eng."
        )
    return requested

"""Vendor-neutral OCR engine contract."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from PIL import Image

from ai.document_ai.models import EnginePageResult


class OcrEngine(Protocol):
    """An OCR engine that recognizes a preprocessed rendered page in memory."""

    def recognize(self, image: Image.Image, *, languages: Sequence[str]) -> EnginePageResult:
        """Recognize one rendered page without storing or mutating its source."""

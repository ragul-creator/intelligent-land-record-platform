"""Lazy adapter for Bodhan AI / AI4Bharat IndicOCR handwriting recognition.

The upstream model is optional and gated by its own license/access terms. This
module does not download weights or install dependencies. Callers provide a
local IndicOCR repository/model path after accepting those terms separately.
"""

from __future__ import annotations

import importlib
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Callable, Protocol

from PIL import Image

from ai.document_ai.errors import OcrEngineError, OcrLanguageUnavailableError
from ai.document_ai.languages import validate_language_codes
from ai.document_ai.models import BoundingBox, EnginePageResult, OcrRegion


INDIC_OCR_HANDWRITING_LANGUAGES = frozenset(
    {"eng", "hin", "ben", "tel", "mar", "tam", "guj", "kan", "mal", "ori", "pan", "asm", "urd"}
)


class IndicOcrParser(Protocol):
    def parse(self, source: str) -> dict[str, Any]: ...


class IndicOcrHtrEngine:
    """Page-level printed/handwritten OCR adapter for a local IndicOCR checkout."""

    name = "indic-ocr-htr"

    def __init__(
        self,
        *,
        model_path: str | Path,
        parser: IndicOcrParser | None = None,
        parser_factory: Callable[[str], IndicOcrParser] | None = None,
    ) -> None:
        self._model_path = Path(model_path).expanduser().resolve()
        self._parser = parser
        self._parser_factory = parser_factory

    def recognize(self, image: Image.Image, *, languages: Sequence[str]) -> EnginePageResult:
        requested = validate_language_codes(languages)
        unsupported = sorted(set(requested) - INDIC_OCR_HANDWRITING_LANGUAGES)
        if unsupported:
            raise OcrLanguageUnavailableError(
                "IndicOCR handwriting mode is not documented for: "
                + ", ".join(unsupported)
                + ". No fallback OCR engine was used."
            )

        parser = self._get_parser()
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as temporary:
            source_path = Path(temporary.name)
        try:
            image.convert("RGB").save(source_path, format="PNG")
            try:
                page = parser.parse(str(source_path))
            except Exception as error:
                raise OcrEngineError(f"IndicOCR handwriting recognition failed: {error}") from error
        finally:
            source_path.unlink(missing_ok=True)

        if not isinstance(page, dict):
            raise OcrEngineError("IndicOCR returned an unsupported page payload.")

        blocks = page.get("blocks")
        if not isinstance(blocks, list):
            raise OcrEngineError("IndicOCR page payload did not contain a blocks list.")

        regions: list[OcrRegion] = []
        text_parts: list[str] = []
        for block in sorted(
            (item for item in blocks if isinstance(item, dict)),
            key=lambda item: int(item.get("order", 0)),
        ):
            text = str(block.get("text") or "").strip()
            if not text:
                continue
            bbox = _bounding_box(block.get("bbox_xyxy"))
            regions.append(
                OcrRegion(
                    text=text,
                    confidence=None,
                    bounding_box=bbox,
                    kind=str(block.get("type") or block.get("label") or "block"),
                )
            )
            text_parts.append(text)

        return EnginePageResult(
            text="\n".join(text_parts),
            confidence=None,
            regions=tuple(regions),
            engine=self.name,
            engine_version=_package_version(),
            model_version="bodhan-ai/indic-ocr",
        )

    def _get_parser(self) -> IndicOcrParser:
        if self._parser is not None:
            return self._parser
        if not self._model_path.exists():
            raise OcrEngineError(
                f"IndicOCR model path does not exist: {self._model_path}. "
                "Download/prepare the gated upstream model separately before using handwriting mode."
            )
        if self._parser_factory is not None:
            self._parser = self._parser_factory(str(self._model_path))
            return self._parser

        model_path_text = str(self._model_path)
        if model_path_text not in sys.path:
            sys.path.insert(0, model_path_text)
        try:
            module = importlib.import_module("indic_ocr")
            parser_type = getattr(module, "IndicOCR")
            self._parser = parser_type.from_pretrained(model_path_text)
        except Exception as error:
            raise OcrEngineError(
                "IndicOCR could not be imported or initialised. Install the upstream dependencies "
                "inside an isolated environment and point this adapter at the prepared model checkout."
            ) from error
        return self._parser


def _bounding_box(value: object) -> BoundingBox | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(item) for item in value)
    except (TypeError, ValueError):
        return None
    left = max(0, round(x0))
    top = max(0, round(y0))
    right = max(left, round(x1))
    bottom = max(top, round(y1))
    return BoundingBox(left=left, top=top, width=right - left, height=bottom - top)


def _package_version() -> str | None:
    try:
        from importlib.metadata import version

        return version("indic-ocr")
    except Exception:
        return None

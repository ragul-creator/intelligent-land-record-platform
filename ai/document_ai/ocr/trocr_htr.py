"""Lazy pretrained TrOCR handwriting-recognition adapter.

The default checkpoint is Microsoft's small TrOCR model fine-tuned on IAM
English handwriting. Heavy model dependencies are optional and imported only
when the adapter is instantiated without injected test doubles.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from ai.document_ai.errors import OcrEngineError, OcrLanguageUnavailableError
from ai.document_ai.languages import validate_language_codes
from ai.document_ai.models import BoundingBox, EnginePageResult, OcrRegion

DEFAULT_TROCR_HANDWRITTEN_MODEL = "microsoft/trocr-small-handwritten"
TROCR_HANDWRITING_LANGUAGES = frozenset({"eng"})


class TrOcrHtrEngine:
    """Single-line English handwriting adapter backed by pretrained TrOCR."""

    name = "trocr-htr"

    def __init__(
        self,
        *,
        model_name_or_path: str | Path = DEFAULT_TROCR_HANDWRITTEN_MODEL,
        device: str = "auto",
        processor: Any | None = None,
        model: Any | None = None,
    ) -> None:
        self._model_name_or_path = str(model_name_or_path)
        self._requested_device = device
        self._processor = processor
        self._model = model
        self._resolved_device: str | None = None

    def recognize(self, image: Image.Image, *, languages: Sequence[str]) -> EnginePageResult:
        requested = validate_language_codes(languages)
        unsupported = sorted(set(requested) - TROCR_HANDWRITING_LANGUAGES)
        if unsupported:
            raise OcrLanguageUnavailableError(
                "The configured TrOCR handwriting checkpoint supports English handwriting only; "
                "unsupported requested languages: "
                + ", ".join(unsupported)
                + ". No fallback OCR engine was used."
            )

        processor, model = self._get_runtime()
        try:
            inputs = processor(images=image.convert("RGB"), return_tensors="pt")
            pixel_values = inputs.pixel_values
            if hasattr(pixel_values, "to"):
                pixel_values = pixel_values.to(self._resolved_device or "cpu")
            generated_ids = model.generate(pixel_values)
            text = processor.batch_decode(generated_ids, skip_special_tokens=True)[0].strip()
        except Exception as error:
            raise OcrEngineError(f"TrOCR handwriting recognition failed: {error}") from error

        regions = ()
        if text:
            regions = (
                OcrRegion(
                    text=text,
                    confidence=None,
                    bounding_box=BoundingBox(
                        left=0,
                        top=0,
                        width=image.width,
                        height=image.height,
                    ),
                    kind="handwritten_line",
                ),
            )

        return EnginePageResult(
            text=text,
            confidence=None,
            regions=regions,
            engine=self.name,
            engine_version=_package_version("transformers"),
            model_version=self._model_name_or_path,
        )

    def _get_runtime(self) -> tuple[Any, Any]:
        if self._processor is not None and self._model is not None:
            if self._resolved_device is None:
                self._resolved_device = "cpu" if self._requested_device == "auto" else self._requested_device
            return self._processor, self._model

        try:
            import torch
            from transformers import TrOCRProcessor, VisionEncoderDecoderModel
        except Exception as error:
            raise OcrEngineError(
                "TrOCR dependencies are not installed. Install "
                "ai/document_ai/requirements-htr.txt before using --engine trocr."
            ) from error

        device = self._requested_device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        if device not in {"cpu", "cuda", "mps"}:
            raise OcrEngineError(f"Unsupported TrOCR device: {device}")

        try:
            self._processor = TrOCRProcessor.from_pretrained(self._model_name_or_path)
            self._model = VisionEncoderDecoderModel.from_pretrained(self._model_name_or_path)
            self._model.to(device)
            self._model.eval()
        except Exception as error:
            raise OcrEngineError(
                f"Could not load TrOCR model {self._model_name_or_path!r}: {error}"
            ) from error

        self._resolved_device = device
        return self._processor, self._model


def _package_version(distribution: str) -> str | None:
    try:
        from importlib.metadata import version

        return version(distribution)
    except Exception:
        return None

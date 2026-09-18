"""OCR engine abstractions and the lazy Tesseract implementation."""

from ai.document_ai.ocr.base import OcrEngine
from ai.document_ai.ocr.tesseract import TesseractOcrEngine

__all__ = ["OcrEngine", "TesseractOcrEngine"]

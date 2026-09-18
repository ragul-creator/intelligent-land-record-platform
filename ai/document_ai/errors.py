"""Domain-specific errors for document loading and preliminary OCR."""


class DocumentAiError(RuntimeError):
    """Base error for the document AI library."""


class DocumentLoadError(DocumentAiError):
    """The supplied local document cannot be read or rendered safely."""


class UnsupportedDocumentError(DocumentLoadError):
    """The supplied document type is not supported by the F.1 loader."""


class OcrEngineError(DocumentAiError):
    """An OCR engine is unavailable or cannot process the page."""


class OcrLanguageConfigurationError(OcrEngineError):
    """A configured Tesseract language identifier is malformed or unsafe."""


class OcrLanguageUnavailableError(OcrEngineError):
    """A requested OCR language pack is not installed in the selected engine."""

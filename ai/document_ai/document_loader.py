"""Safe, ordered in-memory loading for supported document inputs."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, ImageSequence

from ai.document_ai.errors import DocumentLoadError, UnsupportedDocumentError

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}


@dataclass(frozen=True, slots=True)
class SourcePage:
    """One immutable-source page copied to a transient in-memory image."""

    page_number: int
    image: Image.Image


def load_document_pages(source: str | Path, *, dpi: int = 300, allowed_root: str | Path | None = None) -> tuple[SourcePage, ...]:
    """Load supported images or render PDFs in order without writing beside the source."""

    source_path = _resolve_source_path(source, allowed_root=allowed_root)
    if not 72 <= dpi <= 600:
        raise DocumentLoadError("OCR rendering DPI must be between 72 and 600.")
    if source_path.suffix.lower() == ".pdf":
        return tuple(_render_pdf_pages(source_path, dpi=dpi))
    if source_path.suffix.lower() in _IMAGE_SUFFIXES:
        return tuple(_load_image_pages(source_path))
    raise UnsupportedDocumentError("Unsupported document type. F.1 supports PDF, PNG, JPG/JPEG, TIFF, and TIF files.")


def _resolve_source_path(source: str | Path, *, allowed_root: str | Path | None) -> Path:
    try:
        source_path = Path(source).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise DocumentLoadError("The OCR source path does not exist or cannot be resolved.") from error
    if not source_path.is_file():
        raise DocumentLoadError("The OCR source must be a local file.")
    if allowed_root is not None:
        try:
            source_path.relative_to(Path(allowed_root).expanduser().resolve())
        except ValueError as error:
            raise DocumentLoadError("The OCR source is outside the configured allowed root.") from error
    return source_path


def _load_image_pages(source_path: Path) -> Iterator[SourcePage]:
    try:
        with Image.open(source_path) as image:
            for page_number, frame in enumerate(ImageSequence.Iterator(image), start=1):
                # copy detaches the result before the source file is closed.
                yield SourcePage(page_number=page_number, image=ImageOps.exif_transpose(frame).copy())
    except (OSError, ValueError) as error:
        raise DocumentLoadError(f"Unable to read image document '{source_path.name}'.") from error


def _render_pdf_pages(source_path: Path, *, dpi: int) -> Iterator[SourcePage]:
    try:
        import fitz
    except ImportError as error:
        raise DocumentLoadError("PDF support requires PyMuPDF. Install ai/document_ai/requirements.txt.") from error

    try:
        with fitz.open(source_path) as document:
            for page_number, page in enumerate(document, start=1):
                pixel_map = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
                image = Image.frombytes("RGB", (pixel_map.width, pixel_map.height), pixel_map.samples)
                yield SourcePage(page_number=page_number, image=image)
    except Exception as error:
        raise DocumentLoadError(f"Unable to render PDF document '{source_path.name}'.") from error

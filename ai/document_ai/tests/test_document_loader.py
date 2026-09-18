from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from ai.document_ai.document_loader import SourcePage, load_document_pages
from ai.document_ai.errors import DocumentLoadError, UnsupportedDocumentError


@pytest.mark.parametrize("suffix, image_format", [(".png", "PNG"), (".jpg", "JPEG"), (".tiff", "TIFF")])
def test_loads_supported_image_formats(tmp_path: Path, suffix: str, image_format: str) -> None:
    source = tmp_path / f"page{suffix}"
    Image.new("RGB", (24, 12), "white").save(source, format=image_format)

    pages = load_document_pages(source)

    assert [(page.page_number, page.image.size) for page in pages] == [(1, (24, 12))]


def test_tiff_preserves_frame_order(tmp_path: Path) -> None:
    source = tmp_path / "register.tiff"
    first = Image.new("L", (10, 10), 0)
    second = Image.new("L", (10, 10), 255)
    first.save(source, save_all=True, append_images=[second])

    pages = load_document_pages(source)

    assert [page.page_number for page in pages] == [1, 2]
    assert pages[0].image.getpixel((0, 0)) == 0
    assert pages[1].image.getpixel((0, 0)) == 255


def test_pdf_rendering_preserves_mocked_page_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "legacy.pdf"
    source.write_bytes(b"not-a-real-pdf")

    def render_pages(path: Path, *, dpi: int):
        assert path == source.resolve()
        assert dpi == 240
        yield SourcePage(1, Image.new("L", (10, 10), 10))
        yield SourcePage(2, Image.new("L", (10, 10), 20))

    monkeypatch.setattr("ai.document_ai.document_loader._render_pdf_pages", render_pages)
    pages = load_document_pages(source, dpi=240)

    assert [page.page_number for page in pages] == [1, 2]
    assert [page.image.getpixel((0, 0)) for page in pages] == [10, 20]


def test_loader_rejects_path_outside_allowed_root(tmp_path: Path) -> None:
    source = tmp_path / "outside.png"
    Image.new("L", (2, 2), 255).save(source)

    with pytest.raises(DocumentLoadError, match="outside"):
        load_document_pages(source, allowed_root=tmp_path / "uploads")


def test_loader_rejects_unsupported_document(tmp_path: Path) -> None:
    source = tmp_path / "record.docx"
    source.write_bytes(b"placeholder")

    with pytest.raises(UnsupportedDocumentError):
        load_document_pages(source)

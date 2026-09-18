from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw

from ai.document_ai.models import BoundingBox, EnginePageResult, OcrRegion
from ai.document_ai.pipeline import OcrPipeline


class FakeEngine:
    name = "fake-ocr"

    def recognize(self, image: Image.Image, *, languages: Sequence[str]) -> EnginePageResult:
        return EnginePageResult(
            text="preliminary text",
            confidence=0.75,
            regions=(OcrRegion("preliminary", 0.75, BoundingBox(1, 2, 20, 8)),),
            engine=self.name,
            engine_version="test-1",
            model_version=None,
        )


def test_pipeline_preserves_source_metadata_and_does_not_modify_input(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    image = Image.new("RGB", (100, 40), "white")
    ImageDraw.Draw(image).text((4, 10), "source", fill="black")
    image.save(source)
    original_bytes = source.read_bytes()

    result = OcrPipeline(engine=FakeEngine()).process(source, source_id="file-123", allowed_root=tmp_path)

    assert source.read_bytes() == original_bytes
    assert result.source_id == "file-123"
    assert result.status == "OCR_PRELIMINARY"
    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.source_id == "file-123"
    assert page.page_number == 1
    assert page.text == "preliminary text"
    assert page.confidence == 0.75
    assert page.requested_languages == ("tam", "eng")
    assert page.project_tested_languages == ("tam", "eng")
    assert page.processed_at.tzinfo is not None
    payload = result.to_dict()
    assert payload["pages"][0]["regions"][0]["bounding_box"]["left"] == 1
    assert payload["requested_languages"] == ["tam", "eng"]
    assert payload["processed_at"].endswith("Z")


def test_pipeline_preserves_generic_requested_languages_in_provenance(tmp_path: Path) -> None:
    source = tmp_path / "hindi-source.png"
    Image.new("RGB", (30, 20), "white").save(source)

    result = OcrPipeline(engine=FakeEngine()).process(
        source,
        source_id="file-hin",
        languages=("hin", "eng"),
        allowed_root=tmp_path,
    )

    assert result.requested_languages == ("hin", "eng")
    assert result.project_tested_languages == ("tam", "eng")
    assert result.pages[0].requested_languages == ("hin", "eng")

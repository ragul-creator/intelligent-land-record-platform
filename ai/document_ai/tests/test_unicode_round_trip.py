from __future__ import annotations

import json
from pathlib import Path

from PIL import Image

from ai.document_ai.ocr.tesseract import TesseractOcrEngine
from ai.document_ai.pipeline import OcrPipeline

TAMIL_TEXT = "தமிழ்நாடு\nமாவட்டம்\nசென்னை"


class UnicodeBackend:
    def get_languages(self) -> list[str]:
        return ["eng", "tam"]

    def get_version(self) -> str:
        return "5.5.3"

    def image_to_data(self, image: Image.Image, *, language: str, config: str) -> dict[str, list[object]]:
        assert language == "tam+eng"
        return {
            "text": ["தமிழ்நாடு", "மாவட்டம்", "சென்னை"],
            "conf": ["95", "94", "93"],
            "left": [1, 1, 1],
            "top": [1, 12, 24],
            "width": [80, 80, 80],
            "height": [10, 10, 10],
        }

    def image_to_string(self, image: Image.Image, *, language: str, config: str) -> str:
        return TAMIL_TEXT


def test_tamil_unicode_survives_engine_page_document_json_and_utf8_readback(tmp_path: Path) -> None:
    source = tmp_path / "tamil-source.png"
    Image.new("L", (100, 50), 255).save(source)

    result = OcrPipeline(engine=TesseractOcrEngine(backend=UnicodeBackend())).process(
        source,
        source_id="tamil-source",
        languages=("tam", "eng"),
        allowed_root=tmp_path,
    )
    serialized = json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True)
    output = tmp_path / "ocr-result.json"
    output.write_text(serialized, encoding="utf-8")
    read_back = json.loads(output.read_text(encoding="utf-8"))

    assert result.pages[0].text == TAMIL_TEXT
    assert tuple(region.text for region in result.pages[0].regions) == ("தமிழ்நாடு", "மாவட்டம்", "சென்னை")
    assert read_back["pages"][0]["text"] == TAMIL_TEXT
    assert [region["text"] for region in read_back["pages"][0]["regions"]] == ["தமிழ்நாடு", "மாவட்டம்", "சென்னை"]
    assert "à®" not in serialized
    output_bytes = output.read_bytes()
    assert all(token.encode("utf-8") in output_bytes for token in TAMIL_TEXT.splitlines())

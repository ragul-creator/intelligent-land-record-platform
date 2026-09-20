from pathlib import Path

from PIL import Image
import pytest

from ai.document_ai.errors import OcrLanguageUnavailableError
from ai.document_ai.ocr.indic_ocr_htr import IndicOcrHtrEngine


class FakeParser:
    def parse(self, source: str):
        assert Path(source).exists()
        return {
            "width": 800,
            "height": 1200,
            "blocks": [
                {
                    "order": 1,
                    "label": "Paragraph",
                    "type": "Text",
                    "bbox_xyxy": [20.2, 30.4, 210.9, 80.7],
                    "conf": 0.91,
                    "text": "சர்வே எண் 123/4",
                },
                {
                    "order": 0,
                    "label": "Title",
                    "type": "Title",
                    "bbox_xyxy": [10, 5, 180, 25],
                    "conf": 0.88,
                    "text": "Land Record",
                },
            ],
        }


def test_indic_ocr_htr_preserves_reading_order_and_bbox_without_mislabeling_layout_confidence(tmp_path):
    engine = IndicOcrHtrEngine(model_path=tmp_path, parser=FakeParser())

    result = engine.recognize(Image.new("RGB", (320, 200), "white"), languages=("tam", "eng"))

    assert result.engine == "indic-ocr-htr"
    assert result.model_version == "bodhan-ai/indic-ocr"
    assert result.text == "Land Record\nசர்வே எண் 123/4"
    assert result.confidence is None
    assert len(result.regions) == 2
    assert result.regions[0].confidence is None
    assert result.regions[1].bounding_box.left == 20
    assert result.regions[1].bounding_box.top == 30
    assert result.regions[1].bounding_box.width == 191
    assert result.regions[1].bounding_box.height == 51


def test_indic_ocr_htr_rejects_undocumented_handwriting_language(tmp_path):
    engine = IndicOcrHtrEngine(model_path=tmp_path, parser=FakeParser())

    with pytest.raises(OcrLanguageUnavailableError, match="No fallback OCR engine was used"):
        engine.recognize(Image.new("RGB", (10, 10), "white"), languages=("san",))

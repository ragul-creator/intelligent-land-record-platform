from types import SimpleNamespace

from PIL import Image
import pytest

from ai.document_ai.errors import OcrLanguageUnavailableError
from ai.document_ai.ocr.trocr_htr import TrOcrHtrEngine


class FakeTensor:
    def __init__(self):
        self.device = None

    def to(self, device):
        self.device = device
        return self


class FakeProcessor:
    def __init__(self, text="Survey No. 123/4"):
        self.text = text
        self.tensor = FakeTensor()

    def __call__(self, *, images, return_tensors):
        assert images.mode == "RGB"
        assert return_tensors == "pt"
        return SimpleNamespace(pixel_values=self.tensor)

    def batch_decode(self, generated_ids, *, skip_special_tokens):
        assert generated_ids == [[1, 2, 3]]
        assert skip_special_tokens is True
        return [self.text]


class FakeModel:
    def generate(self, pixel_values):
        assert pixel_values.device == "cpu"
        return [[1, 2, 3]]


def test_trocr_htr_returns_traceable_full_line_region():
    processor = FakeProcessor()
    engine = TrOcrHtrEngine(
        processor=processor,
        model=FakeModel(),
        model_name_or_path="microsoft/trocr-small-handwritten",
    )

    result = engine.recognize(Image.new("L", (240, 64), "white"), languages=("eng",))

    assert result.engine == "trocr-htr"
    assert result.model_version == "microsoft/trocr-small-handwritten"
    assert result.text == "Survey No. 123/4"
    assert result.confidence is None
    assert len(result.regions) == 1
    assert result.regions[0].confidence is None
    assert result.regions[0].kind == "handwritten_line"
    assert result.regions[0].bounding_box.width == 240
    assert result.regions[0].bounding_box.height == 64


def test_trocr_htr_rejects_tamil_instead_of_falling_back():
    engine = TrOcrHtrEngine(processor=FakeProcessor(), model=FakeModel())

    with pytest.raises(OcrLanguageUnavailableError, match="English handwriting only"):
        engine.recognize(Image.new("RGB", (10, 10), "white"), languages=("tam", "eng"))

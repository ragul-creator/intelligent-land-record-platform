import json

from PIL import Image

from ai.document_ai.benchmark import character_error_rate, evaluate_manifest, word_error_rate
from ai.document_ai.models import BoundingBox, EnginePageResult, OcrRegion


class FakeEngine:
    name = "fake-benchmark"

    def recognize(self, image, *, languages):
        return EnginePageResult(
            text="Survey No. 123/4",
            confidence=0.8,
            regions=(
                OcrRegion("Survey", 0.9, BoundingBox(10, 10, 40, 12)),
                OcrRegion("No.", 0.9, BoundingBox(55, 10, 20, 12)),
                OcrRegion("123/4", 0.6, BoundingBox(80, 10, 35, 12)),
            ),
            engine=self.name,
            engine_version="test",
            model_version="fake-htr-v1",
        )


def test_error_rates_are_zero_for_exact_match():
    assert character_error_rate("abc", "abc") == 0
    assert word_error_rate("one two", "one two") == 0


def test_benchmark_records_ocr_and_field_metrics(tmp_path):
    image_path = tmp_path / "sample.png"
    Image.new("RGB", (160, 80), "white").save(image_path)
    manifest = {
        "version": 1,
        "samples": [
            {
                "id": "handwritten-demo",
                "path": "sample.png",
                "category": "handwritten",
                "languages": ["tam", "eng"],
                "ground_truth_text": "Survey No. 123/4",
                "expected_fields": {"survey_number": "123/4"},
            }
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = evaluate_manifest(manifest_path, engine=FakeEngine())

    assert report["sample_count"] == 1
    assert report["completed_count"] == 1
    assert report["mean_character_error_rate"] == 0
    assert report["mean_word_error_rate"] == 0
    assert report["field_exact_match_accuracy"] == 1
    sample = report["samples"][0]
    assert sample["category"] == "handwritten"
    assert sample["model_version"] == "fake-htr-v1"
    assert sample["ocr_confidence"] == 0.8
    assert sample["field_details"]["survey_number"]["exact_match"] is True

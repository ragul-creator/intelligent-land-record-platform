from __future__ import annotations

from PIL import Image, ImageDraw

from ai.document_ai.preprocessing.image import PreprocessingConfig, detect_skew_angle, preprocess_image


def test_preprocess_image_returns_grayscale_derivative_without_mutating_source() -> None:
    source = Image.new("RGB", (160, 80), "white")
    ImageDraw.Draw(source).text((10, 20), "Record", fill="black")
    source_bytes = source.tobytes()

    processed, metadata = preprocess_image(source, PreprocessingConfig(threshold=True))

    assert source.mode == "RGB"
    assert source.tobytes() == source_bytes
    assert processed.mode == "L"
    assert processed.size == source.size
    assert "grayscale" in metadata.operations
    assert "thresholded" in metadata.operations


def test_blank_page_has_no_skew_and_does_not_crash() -> None:
    blank = Image.new("L", (100, 100), 255)

    processed, metadata = preprocess_image(blank)

    assert processed.size == blank.size
    assert metadata.deskew_angle_degrees is None
    assert "deskewed" not in metadata.operations
    assert detect_skew_angle(__import__("numpy").asarray(blank)) is None

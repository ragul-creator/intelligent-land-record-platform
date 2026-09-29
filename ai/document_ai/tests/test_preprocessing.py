from __future__ import annotations

from PIL import Image, ImageDraw

from ai.document_ai.preprocessing.image import PreprocessingConfig, detect_skew_angle, preprocess_image


def test_preprocess_image_returns_grayscale_derivative_without_mutating_source() -> None:
    source = Image.new("RGB", (160, 80), "white")
    ImageDraw.Draw(source).text((10, 20), "Record", fill="black")
    source_bytes = source.tobytes()

    processed, metadata = preprocess_image(source, PreprocessingConfig(threshold=True, upscale_small_images=False))

    assert source.mode == "RGB"
    assert source.tobytes() == source_bytes
    assert processed.mode == "L"
    assert processed.size == source.size
    assert "grayscale" in metadata.operations
    assert "thresholded" in metadata.operations


def test_blank_page_has_no_skew_and_does_not_crash() -> None:
    blank = Image.new("L", (100, 100), 255)

    processed, metadata = preprocess_image(blank, PreprocessingConfig(upscale_small_images=False))

    assert processed.size == blank.size
    assert metadata.deskew_angle_degrees is None
    assert "deskewed" not in metadata.operations
    assert detect_skew_angle(__import__("numpy").asarray(blank)) is None


def test_small_document_photo_is_upscaled_before_ocr_enhancements() -> None:
    source = Image.new("RGB", (658, 1024), "white")

    processed, metadata = preprocess_image(
        source,
        PreprocessingConfig(enhance_contrast=False, denoise=False, deskew=False),
    )

    assert processed.width == 1400
    assert processed.height == 2179
    assert "upscaled_for_ocr" in metadata.operations


def test_ocr_upscale_is_bounded_for_very_small_images() -> None:
    source = Image.new("RGB", (200, 100), "white")

    processed, metadata = preprocess_image(
        source,
        PreprocessingConfig(enhance_contrast=False, denoise=False, deskew=False),
    )

    assert processed.size == (500, 250)
    assert "upscaled_for_ocr" in metadata.operations

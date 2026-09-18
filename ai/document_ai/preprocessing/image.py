"""Conservative in-memory preprocessing for OCR page derivatives."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageEnhance, ImageOps

from ai.document_ai.models import PreprocessingMetadata


@dataclass(frozen=True, slots=True)
class PreprocessingConfig:
    """Optional enhancements; thresholding remains opt-in because it can lose detail."""

    enhance_contrast: bool = True
    denoise: bool = True
    deskew: bool = True
    threshold: bool = False
    max_deskew_degrees: float = 15.0


def preprocess_image(image: Image.Image, config: PreprocessingConfig | None = None) -> tuple[Image.Image, PreprocessingMetadata]:
    """Return a new grayscale derivative and metadata without modifying ``image``."""

    options = config or PreprocessingConfig()
    normalized = ImageOps.exif_transpose(image).convert("L")
    operations = ["orientation_normalized", "grayscale"]

    if options.enhance_contrast:
        normalized = ImageEnhance.Contrast(normalized).enhance(1.5)
        operations.append("contrast_enhanced")

    pixels = np.asarray(normalized)
    if options.denoise:
        pixels = _denoise(pixels)
        normalized = Image.fromarray(pixels, mode="L")
        operations.append("denoised")

    deskew_angle: float | None = None
    if options.deskew:
        deskew_angle = detect_skew_angle(np.asarray(normalized), options.max_deskew_degrees)
        if deskew_angle is not None:
            normalized = normalized.rotate(-deskew_angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor=255)
            operations.append("deskewed")

    if options.threshold:
        thresholded = _otsu_threshold(np.asarray(normalized))
        normalized = Image.fromarray(thresholded, mode="L")
        operations.append("thresholded")

    return normalized, PreprocessingMetadata(tuple(operations), deskew_angle)


def detect_skew_angle(grayscale: np.ndarray, max_degrees: float = 15.0) -> float | None:
    """Estimate a modest text-page skew, returning ``None`` when evidence is insufficient."""

    if grayscale.ndim != 2 or grayscale.size == 0:
        return None
    foreground = grayscale < _otsu_value(grayscale)
    coordinates = np.column_stack(np.nonzero(foreground))
    if len(coordinates) < 32:
        return None

    centered = coordinates.astype(np.float64) - coordinates.mean(axis=0)
    _, _, vectors = np.linalg.svd(centered, full_matrices=False)
    direction = vectors[0]
    angle = float(np.degrees(np.arctan2(direction[0], direction[1])))
    if angle > 45.0:
        angle -= 90.0
    elif angle < -45.0:
        angle += 90.0
    if not 0.15 <= abs(angle) <= max_degrees:
        return None
    return angle


def _denoise(grayscale: np.ndarray) -> np.ndarray:
    """A tiny median filter avoids an OpenCV import for a simple, safe default."""

    padded = np.pad(grayscale, 1, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, (3, 3))
    return np.median(windows, axis=(-2, -1)).astype(np.uint8)


def _otsu_threshold(grayscale: np.ndarray) -> np.ndarray:
    threshold = _otsu_value(grayscale)
    return np.where(grayscale > threshold, 255, 0).astype(np.uint8)


def _otsu_value(grayscale: np.ndarray) -> int:
    histogram = np.bincount(grayscale.ravel(), minlength=256).astype(np.float64)
    total = grayscale.size
    if total == 0 or np.count_nonzero(histogram) <= 1:
        return 127
    cumulative_weight = np.cumsum(histogram)
    cumulative_mean = np.cumsum(histogram * np.arange(256))
    total_mean = cumulative_mean[-1]
    denominator = cumulative_weight * (total - cumulative_weight)
    variance = np.zeros(256, dtype=np.float64)
    valid = denominator > 0
    variance[valid] = (total_mean * cumulative_weight[valid] - cumulative_mean[valid]) ** 2 / denominator[valid]
    return int(np.argmax(variance))

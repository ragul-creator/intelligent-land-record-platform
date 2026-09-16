"""Safe binary building-segmentation metrics; pixel accuracy is intentionally omitted."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import torch
from torch import Tensor


@dataclass(frozen=True)
class SegmentationMetrics:
    iou: float
    dice: float
    precision: float
    recall: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def binary_confusion(prediction: Tensor, target: Tensor, *, threshold: float = 0.5) -> tuple[int, int, int]:
    """Return true-positive, false-positive, and false-negative pixel counts."""
    if not 0.0 <= threshold <= 1.0:
        raise ValueError("threshold must be between 0 and 1.")
    predicted = (prediction >= threshold).to(torch.bool)
    actual = target.to(torch.bool)
    true_positive = (predicted & actual).sum().item()
    false_positive = (predicted & ~actual).sum().item()
    false_negative = (~predicted & actual).sum().item()
    return int(true_positive), int(false_positive), int(false_negative)


def metrics_from_confusion(true_positive: int, false_positive: int, false_negative: int, *, epsilon: float = 1e-7) -> SegmentationMetrics:
    """Compute binary metrics with sensible empty-mask behavior from total counts."""
    union = true_positive + false_positive + false_negative
    iou = 1.0 if union == 0 else true_positive / (union + epsilon)
    dice_denominator = 2 * true_positive + false_positive + false_negative
    dice = 1.0 if dice_denominator == 0 else (2 * true_positive) / (dice_denominator + epsilon)
    precision_denominator = true_positive + false_positive
    recall_denominator = true_positive + false_negative
    precision = 1.0 if precision_denominator == 0 and false_negative == 0 else true_positive / (precision_denominator + epsilon)
    recall = 1.0 if recall_denominator == 0 else true_positive / (recall_denominator + epsilon)
    return SegmentationMetrics(iou, dice, precision, recall)


def binary_metrics(prediction: Tensor, target: Tensor, *, threshold: float = 0.5, epsilon: float = 1e-7) -> SegmentationMetrics:
    """Compute aggregate binary metrics for a tensor batch."""
    return metrics_from_confusion(*binary_confusion(prediction, target, threshold=threshold), epsilon=epsilon)

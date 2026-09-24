from pathlib import Path

import numpy as np
import pytest
from PIL import Image
import torch

from ai.geoai.segmentation.building_dataset import (
    DatasetValidationError,
    WHUBuildingDataset,
    _percentile_normalize_rgb,
    validate_whu_dataset,
)
from ai.geoai.segmentation.metrics import SegmentationMetrics, binary_confusion, binary_metrics
from ai.geoai.segmentation.model import create_model, load_checkpoint, save_checkpoint
from ai.geoai.segmentation.pipeline import _best_threshold


def write_whu_sample(root: Path, split: str, name: str, *, mask_value: int = 255) -> None:
    image_dir, mask_dir = root / split / "Image", root / split / "Mask"
    image_dir.mkdir(parents=True, exist_ok=True)
    mask_dir.mkdir(parents=True, exist_ok=True)
    Image.fromarray(np.full((32, 48, 3), 128, dtype=np.uint8), mode="RGB").save(image_dir / f"{name}.png")
    Image.fromarray(np.full((32, 48), mask_value, dtype=np.uint8), mode="L").save(mask_dir / f"{name}.png")


def test_dataset_pairs_masks_deterministically_and_binarizes(tmp_path: Path) -> None:
    write_whu_sample(tmp_path, "train", "b", mask_value=7)
    write_whu_sample(tmp_path, "train", "a", mask_value=0)
    check, samples = validate_whu_dataset(tmp_path, "train")
    dataset = WHUBuildingDataset(tmp_path, "train")

    assert check.pair_count == 2
    assert [sample.sample_id for sample in samples] == ["a", "b"]
    assert dataset[0]["image"].shape == (3, 32, 48)
    assert set(dataset[1]["mask"].unique().tolist()) == {1.0}


def test_dataset_reports_missing_pairs(tmp_path: Path) -> None:
    write_whu_sample(tmp_path, "train", "present")
    (tmp_path / "train" / "Mask" / "present.png").unlink()
    with pytest.raises(DatasetValidationError, match="do not match"):
        validate_whu_dataset(tmp_path, "train")


def test_augmentation_is_restricted_to_training_split(tmp_path: Path) -> None:
    for split, names in (("train", ("a", "b")), ("val", ("b",))):
        for name in names:
            write_whu_sample(tmp_path, split, name)
        for name in names:
            image_dir = tmp_path / split / "Image"
            Image.fromarray(np.tile(np.arange(48, dtype=np.uint8), (32, 1)).reshape(32, 48, 1).repeat(3, axis=2), mode="RGB").save(image_dir / f"{name}.png")

    train = WHUBuildingDataset(tmp_path, "train", augment=True)
    validation = WHUBuildingDataset(tmp_path, "val", augment=True)

    assert not torch.equal(train[1]["image"], validation[0]["image"])


def test_metrics_cover_simple_and_empty_masks() -> None:
    perfect = binary_metrics(torch.tensor([[[[0.0, 1.0]]]]), torch.tensor([[[[0.0, 1.0]]]]))
    empty = binary_metrics(torch.zeros((1, 1, 2, 2)), torch.zeros((1, 1, 2, 2)))
    assert perfect.iou == pytest.approx(1.0)
    assert perfect.dice == pytest.approx(1.0)
    assert empty.to_dict() == {"iou": 1.0, "dice": 1.0, "precision": 1.0, "recall": 1.0}


def test_thresholding_behavior_is_explicit() -> None:
    counts = binary_confusion(torch.tensor([[[[0.49, 0.50]]]]), torch.tensor([[[[0.0, 1.0]]]]), threshold=0.5)
    assert counts == (1, 0, 0)

def test_percentile_preprocessing_stretches_each_rgb_band() -> None:
    values = np.arange(100, dtype=np.uint8).reshape(10, 10)
    image = Image.fromarray(
        np.stack((values, values + 20, values + 40), axis=-1),
        mode="RGB",
    )

    normalized = _percentile_normalize_rgb(image)

    assert normalized.shape == (3, 10, 10)
    assert np.all(normalized >= 0.0)
    assert np.all(normalized <= 1.0)
    assert normalized[:, 0, 0].max() == pytest.approx(0.0)
    assert normalized[:, -1, -1].min() == pytest.approx(1.0)


def test_threshold_selection_prefers_best_validation_iou() -> None:
    sweep = {
        0.4: SegmentationMetrics(0.70, 0.82, 0.80, 0.84),
        0.5: SegmentationMetrics(0.76, 0.86, 0.88, 0.84),
        0.6: SegmentationMetrics(0.72, 0.84, 0.93, 0.77),
    }

    threshold, metrics = _best_threshold(sweep)

    assert threshold == 0.5
    assert metrics.iou == pytest.approx(0.76)



def test_cpu_model_probability_and_checkpoint_round_trip(tmp_path: Path) -> None:
    device = torch.device("cpu")
    model = create_model(device=device)
    model.eval()
    image = torch.rand((1, 3, 64, 64))
    probability = model.probabilities(image)
    checkpoint = save_checkpoint(tmp_path / "model.pt", model, epoch=0)
    loaded, payload = load_checkpoint(checkpoint, device=device)

    assert probability.shape == (1, 1, 64, 64)
    assert torch.all((probability >= 0) & (probability <= 1))
    assert payload["epoch"] == 0
    assert loaded.config.model_version == model.config.model_version

"""WHU building image/mask pairing and reproducible pixel-space preprocessing."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image


class DatasetValidationError(ValueError):
    """Clear dataset structure or pairing validation error."""


@dataclass(frozen=True)
class WHUSample:
    sample_id: str
    image_path: Path
    mask_path: Path


@dataclass(frozen=True)
class DatasetCheck:
    split: str
    image_count: int
    mask_count: int
    pair_count: int
    unmatched_images: tuple[str, ...]
    unmatched_masks: tuple[str, ...]
    image_size: tuple[int, int] | None
    image_mode: str | None
    image_channel_count: int | None
    mask_size: tuple[int, int] | None

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["unmatched_images"] = list(self.unmatched_images)
        result["unmatched_masks"] = list(self.unmatched_masks)
        return result


def _split_paths(root: str | Path, split: str) -> tuple[Path, Path]:
    if split not in {"train", "val", "test"}:
        raise DatasetValidationError("Split must be one of: train, val, test.")
    dataset_root = Path(root)
    image_dir = dataset_root / split / "Image"
    mask_dir = dataset_root / split / "Mask"
    if not image_dir.is_dir() or not mask_dir.is_dir():
        raise DatasetValidationError(f"Expected {image_dir} and {mask_dir} directories.")
    return image_dir, mask_dir


def _png_by_stem(directory: Path) -> dict[str, Path]:
    return {path.stem: path for path in sorted(directory.glob("*.png"), key=lambda candidate: candidate.name.lower())}


def validate_whu_dataset(root: str | Path, split: str) -> tuple[DatasetCheck, tuple[WHUSample, ...]]:
    """Validate a WHU split before loading it; pairing errors are never silently ignored."""
    image_dir, mask_dir = _split_paths(root, split)
    images = _png_by_stem(image_dir)
    masks = _png_by_stem(mask_dir)
    unmatched_images = tuple(sorted(set(images) - set(masks)))
    unmatched_masks = tuple(sorted(set(masks) - set(images)))
    if unmatched_images or unmatched_masks:
        raise DatasetValidationError(
            f"Image/mask pairs do not match: {len(unmatched_images)} image-only, {len(unmatched_masks)} mask-only."
        )
    samples = tuple(WHUSample(stem, images[stem], masks[stem]) for stem in sorted(images))
    image_size = image_mode = image_channel_count = mask_size = None
    if samples:
        with Image.open(samples[0].image_path) as image, Image.open(samples[0].mask_path) as mask:
            image_size, image_mode, image_channel_count, mask_size = image.size, image.mode, len(image.getbands()), mask.size
        for sample in samples:
            with Image.open(sample.image_path) as image, Image.open(sample.mask_path) as mask:
                if image.size != mask.size:
                    raise DatasetValidationError(f"Image/mask dimensions differ for sample {sample.sample_id}.")
                if len(image.getbands()) not in {1, 3, 4}:
                    raise DatasetValidationError(f"Unsupported image channel count for sample {sample.sample_id}.")
    return (
        DatasetCheck(split, len(images), len(masks), len(samples), unmatched_images, unmatched_masks, image_size, image_mode, image_channel_count, mask_size),
        samples,
    )


class WHUBuildingDataset:
    """Deterministic WHU RGB/mask dataset with optional train-only augmentation callback."""

    def __init__(
        self,
        root: str | Path,
        split: str,
        *,
        image_size: int | None = None,
        augment: bool = False,
        limit: int | None = None,
        transform: Callable[[Image.Image, Image.Image], tuple[Image.Image, Image.Image]] | None = None,
    ) -> None:
        if limit is not None and limit <= 0:
            raise ValueError("limit must be greater than zero when supplied.")
        if image_size is not None and image_size <= 0:
            raise ValueError("image_size must be greater than zero when supplied.")
        check, samples = validate_whu_dataset(root, split)
        if not samples:
            raise DatasetValidationError(f"WHU {split} split has no paired PNG samples.")
        self.check = check
        self.samples = samples[:limit] if limit is not None else samples
        self.image_size = image_size
        self.augment = augment and split == "train"
        self.transform = transform

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> dict[str, object]:
        import torch

        sample = self.samples[index]
        with Image.open(sample.image_path) as source_image, Image.open(sample.mask_path) as source_mask:
            image = source_image.convert("RGB")
            mask = source_mask.convert("L")
            if image.size != mask.size:
                raise DatasetValidationError(f"Image/mask dimensions differ for sample {sample.sample_id}.")
            if self.transform is not None:
                image, mask = self.transform(image, mask)
            elif self.augment and index % 2 == 1:
                image, mask = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT), mask.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
            if self.image_size is not None:
                image = image.resize((self.image_size, self.image_size), Image.Resampling.BILINEAR)
                mask = mask.resize((self.image_size, self.image_size), Image.Resampling.NEAREST)
            image_array = np.array(image, dtype=np.float32, copy=True).transpose(2, 0, 1) / 255.0
            image_tensor = torch.from_numpy(image_array)
            image_tensor = (image_tensor - torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)) / torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
            mask_tensor = torch.from_numpy(np.array(mask, dtype=np.uint8, copy=True)).unsqueeze(0).gt(0).to(dtype=image_tensor.dtype)
        return {"image": image_tensor, "mask": mask_tensor, "sample_id": sample.sample_id, "filename": sample.image_path.name}

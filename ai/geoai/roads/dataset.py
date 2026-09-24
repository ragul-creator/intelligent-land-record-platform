"""Portable SpaceNet-style image and mask/vector-label dataset checks."""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform
from pyproj import CRS, Transformer
import torch
from torch.nn import functional as torch_functional
from torch.utils.data import Dataset


class RoadDatasetError(ValueError):
    """Raised for an incomplete or unsupported road-training layout."""


@dataclass(frozen=True)
class RoadDatasetCheck:
    split: str
    image_count: int
    label_count: int
    pair_count: int
    vector_label_count: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _directories(root: str | Path, split: str) -> tuple[Path, Path]:
    if split not in {"train", "val", "test"}:
        raise RoadDatasetError("Split must be one of: train, val, test.")
    images, labels = Path(root) / split / "images", Path(root) / split / "labels"
    if not images.is_dir() or not labels.is_dir():
        raise RoadDatasetError(f"Expected {images} and {labels} directories.")
    return images, labels


def validate_road_dataset(root: str | Path, split: str) -> RoadDatasetCheck:
    """Validate portable image/mask or image/GeoJSON label pairs without loading a model."""
    images_dir, labels_dir = _directories(root, split)
    images = {path.stem: path for path in images_dir.iterdir() if path.suffix.lower() in {".tif", ".tiff", ".png", ".jpg", ".jpeg"}}
    labels = {path.stem: path for path in labels_dir.iterdir() if path.suffix.lower() in {".tif", ".tiff", ".png", ".geojson", ".json"}}
    missing = sorted(set(images) - set(labels))
    extra = sorted(set(labels) - set(images))
    if missing or extra:
        raise RoadDatasetError(f"Road image/label pairs do not match: {len(missing)} image-only, {len(extra)} label-only.")
    for stem, image_path in images.items():
        label_path = labels[stem]
        if label_path.suffix.lower() in {".png", ".tif", ".tiff"}:
            with rasterio.open(image_path) as image, rasterio.open(label_path) as label:
                if image.width != label.width or image.height != label.height:
                    raise RoadDatasetError(f"Image/mask dimensions differ for sample {stem}.")
    return RoadDatasetCheck(split, len(images), len(labels), len(images), sum(path.suffix.lower() in {".geojson", ".json"} for path in labels.values()))



def rasterize_vector_label(
    image_path: str | Path,
    label_path: str | Path,
    *,
    road_half_width_m: float = 1.5,
) -> np.ndarray:
    """Rasterize GeoJSON road centerlines as metric-width road surfaces."""

    with rasterio.open(image_path) as image:
        if image.crs is None:
            raise RoadDatasetError("Road image must have a CRS.")

        payload = json.loads(Path(label_path).read_text(encoding="utf-8"))
        features = (
            payload.get("features", [])
            if payload.get("type") == "FeatureCollection"
            else [payload]
        )

        geometries = [
            shape(item["geometry"] if item.get("type") == "Feature" else item)
            for item in features
            if item.get("geometry")
            or item.get("type") in {"LineString", "MultiLineString"}
        ]

        if not geometries:
            return np.zeros((image.height, image.width), dtype=np.uint8)

        source_crs = CRS.from_user_input(image.crs)

        centre_x = (image.bounds.left + image.bounds.right) / 2.0
        centre_y = (image.bounds.bottom + image.bounds.top) / 2.0
        if source_crs.is_geographic:
            centre_lon, centre_lat = centre_x, centre_y
        else:
            to_wgs84 = Transformer.from_crs(
                source_crs, CRS.from_epsg(4326), always_xy=True
            )
            centre_lon, centre_lat = to_wgs84.transform(centre_x, centre_y)
        zone = max(1, min(60, int((centre_lon + 180.0) // 6.0) + 1))
        epsg = 32600 + zone if centre_lat >= 0 else 32700 + zone
        metric_crs = CRS.from_epsg(epsg)

        to_metric = Transformer.from_crs(
            source_crs, metric_crs, always_xy=True
        ).transform

        from_metric = Transformer.from_crs(
            metric_crs, source_crs, always_xy=True
        ).transform

        buffered = []

        for geometry in geometries:
            metric_geometry = shapely_transform(to_metric, geometry)
            metric_geometry = metric_geometry.buffer(
                road_half_width_m,
                cap_style=2,
                join_style=2,
            )
            buffered.append(
                shapely_transform(from_metric, metric_geometry)
            )

        return rasterize(
            ((geometry, 1) for geometry in buffered),
            out_shape=(image.height, image.width),
            transform=image.transform,
            fill=0,
            dtype=np.uint8,
        )



def _normalize_rgb_bands(
    bands: np.ndarray,
    *,
    nodata: float | int | None = None,
) -> np.ndarray:
    """Match runtime percentile stretching before ImageNet normalization."""
    channels: list[np.ndarray] = []
    for band in bands[:3].astype(np.float32, copy=False):
        finite = np.isfinite(band)
        if nodata is not None:
            finite &= band != nodata
        if not finite.any():
            channels.append(np.zeros(band.shape, dtype=np.float32))
            continue
        low, high = np.percentile(band[finite], (2, 98))
        if high <= low:
            high = low + 1.0
        channels.append(
            np.clip((band - low) / (high - low), 0.0, 1.0).astype(
                np.float32, copy=False
            )
        )
    while len(channels) < 3:
        channels.append(channels[-1])
    return np.stack(channels[:3]).astype(np.float32, copy=False)


def _augment_pair(
    bands: np.ndarray,
    mask: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Apply orientation-safe aerial augmentation to image and mask together."""
    turns = random.randrange(4)
    if turns:
        bands = np.rot90(bands, turns, axes=(1, 2))
        mask = np.rot90(mask, turns, axes=(0, 1))
    if random.random() < 0.5:
        bands = np.flip(bands, axis=2)
        mask = np.flip(mask, axis=1)
    if random.random() < 0.5:
        bands = np.flip(bands, axis=1)
        mask = np.flip(mask, axis=0)
    return bands.copy(), mask.copy()


class RoadSegmentationDataset(Dataset[dict[str, torch.Tensor]]):
    """Paired raster/vector-road labels with no machine-specific dataset paths."""

    def __init__(
        self,
        root: str | Path,
        split: str,
        *,
        image_size: int | None = None,
        limit: int | None = None,
        augment: bool = False,
    ) -> None:
        images_dir, labels_dir = _directories(root, split)
        validate_road_dataset(root, split)
        image_paths = sorted(path for path in images_dir.iterdir() if path.suffix.lower() in {".tif", ".tiff", ".png", ".jpg", ".jpeg"})
        self.samples = [(path, next(labels_dir / f"{path.stem}{suffix}" for suffix in (".tif", ".tiff", ".png", ".geojson", ".json") if (labels_dir / f"{path.stem}{suffix}").is_file())) for path in image_paths]
        self.image_size = image_size
        self.augment = augment and split == "train"
        self.samples = self.samples[:limit] if limit is not None else self.samples
        if not self.samples:
            raise RoadDatasetError(f"No road image/label pairs found for split {split}.")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        image_path, label_path = self.samples[index]
        with rasterio.open(image_path) as dataset:
            bands = dataset.read(
                list(range(1, min(dataset.count, 3) + 1))
            ).astype(np.float32)
            nodata = dataset.nodata
        if bands.shape[0] == 1:
            bands = np.repeat(bands, 3, axis=0)
        elif bands.shape[0] == 2:
            bands = np.concatenate((bands, bands[:1]), axis=0)
        if label_path.suffix.lower() in {".geojson", ".json"}:
            mask_array = rasterize_vector_label(image_path, label_path)
        else:
            with rasterio.open(label_path) as label:
                mask_array = label.read(1)
        mask_array = (mask_array > 0).astype(np.float32)
        if self.augment:
            bands, mask_array = _augment_pair(bands, mask_array)
        normalized = _normalize_rgb_bands(bands, nodata=nodata)
        image = torch.from_numpy(normalized)
        image = (
            image
            - torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
        ) / torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
        mask = torch.from_numpy(mask_array.copy()).unsqueeze(0)
        if self.image_size is not None:
            size = (self.image_size, self.image_size)
            image = torch_functional.interpolate(image.unsqueeze(0), size=size, mode="bilinear", align_corners=False).squeeze(0)
            mask = torch_functional.interpolate(mask.unsqueeze(0), size=size, mode="nearest").squeeze(0)
        return {"image": image, "mask": mask}




"""Portable SpaceNet-style image and mask/vector-label dataset checks."""

from __future__ import annotations

import json
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

        centre_lon = (image.bounds.left + image.bounds.right) / 2.0
        centre_lat = (image.bounds.bottom + image.bounds.top) / 2.0
        zone = int((centre_lon + 180.0) // 6.0) + 1
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



class RoadSegmentationDataset(Dataset[dict[str, torch.Tensor]]):
    """Paired raster/vector-road labels with no machine-specific dataset paths."""

    def __init__(self, root: str | Path, split: str, *, image_size: int | None = None, limit: int | None = None) -> None:
        images_dir, labels_dir = _directories(root, split)
        validate_road_dataset(root, split)
        image_paths = sorted(path for path in images_dir.iterdir() if path.suffix.lower() in {".tif", ".tiff", ".png", ".jpg", ".jpeg"})
        self.samples = [(path, next(labels_dir / f"{path.stem}{suffix}" for suffix in (".tif", ".tiff", ".png", ".geojson", ".json") if (labels_dir / f"{path.stem}{suffix}").is_file())) for path in image_paths]
        self.image_size = image_size
        self.samples = self.samples[:limit] if limit is not None else self.samples
        if not self.samples:
            raise RoadDatasetError(f"No road image/label pairs found for split {split}.")

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, index: int) -> dict[str, torch.Tensor]:
        image_path, label_path = self.samples[index]
        with rasterio.open(image_path) as dataset:
            bands = dataset.read(list(range(1, min(dataset.count, 3) + 1))).astype(np.float32)
        if bands.shape[0] == 1:
            bands = np.repeat(bands, 3, axis=0)
        elif bands.shape[0] == 2:
            bands = np.concatenate((bands, bands[:1]), axis=0)
        image = torch.from_numpy(bands[:3])
        maximum = image.amax(dim=(1, 2), keepdim=True).clamp_min(1.0)
        image = image / maximum
        image = (image - torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)) / torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
        if label_path.suffix.lower() in {".geojson", ".json"}:
            mask_array = rasterize_vector_label(image_path, label_path)
        else:
            with rasterio.open(label_path) as label:
                mask_array = label.read(1)
        mask = torch.from_numpy((mask_array > 0).astype(np.float32)).unsqueeze(0)
        if self.image_size is not None:
            size = (self.image_size, self.image_size)
            image = torch_functional.interpolate(image.unsqueeze(0), size=size, mode="bilinear", align_corners=False).squeeze(0)
            mask = torch_functional.interpolate(mask.unsqueeze(0), size=size, mode="nearest").squeeze(0)
        return {"image": image, "mask": mask}




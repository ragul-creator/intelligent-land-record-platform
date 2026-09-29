"""SegFormer-B2 WorldCover LULC inference for compatible registered GeoTIFFs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import math
from pathlib import Path

import numpy as np
import rasterio
import torch
import torch.nn.functional as F
from pyproj import CRS, Geod, Transformer
from rasterio.features import shapes, sieve
from rasterio.windows import Window
from shapely.geometry import shape
from shapely.ops import transform as shapely_transform, unary_union
from transformers import SegformerForSemanticSegmentation

from ai.geoai.segmentation.runtime import select_device

PATCH_SIZE = 512
MODEL_VERSION = "segformer-b2-worldcover-2021-tamilnadu-v1"
CLASS_CODES = (10, 20, 30, 40, 50, 60, 70, 80, 90, 95, 100)
CLASS_NAMES = {
    10: "TREE_COVER",
    20: "SHRUBLAND",
    30: "GRASSLAND",
    40: "CROPLAND",
    50: "BUILT_UP",
    60: "BARE_SPARSE_VEGETATION",
    70: "SNOW_ICE",
    80: "PERMANENT_WATER_BODIES",
    90: "HERBACEOUS_WETLAND",
    95: "MANGROVES",
    100: "MOSS_LICHEN",
}
MEAN = np.array([0.485, 0.456, 0.406, 0.50], dtype=np.float32)[:, None, None]
STD = np.array([0.229, 0.224, 0.225, 0.25], dtype=np.float32)[:, None, None]


@dataclass(frozen=True)
class LULCFeature:
    geometry: object
    land_use_class: str
    confidence: float | None
    area_m2: float
    area_sqft: float
    model_version: str
    processed_at: str


@dataclass(frozen=True)
class LULCResult:
    features: tuple[LULCFeature, ...]
    source_crs: str
    processing_parameters: dict[str, object]
def _pixel_size_m(dataset: rasterio.io.DatasetReader) -> tuple[float, float]:
    crs = CRS.from_user_input(dataset.crs)
    if crs.is_projected:
        factor = 1.0
        if crs.axis_info and crs.axis_info[0].unit_conversion_factor:
            factor = float(crs.axis_info[0].unit_conversion_factor)
        return abs(float(dataset.transform.a)) * factor, abs(float(dataset.transform.e)) * factor

    geod = Geod(ellps="WGS84")
    center_x = (dataset.bounds.left + dataset.bounds.right) / 2.0
    center_y = (dataset.bounds.bottom + dataset.bounds.top) / 2.0
    _, _, dx = geod.inv(center_x, center_y, center_x + abs(float(dataset.transform.a)), center_y)
    _, _, dy = geod.inv(center_x, center_y, center_x, center_y + abs(float(dataset.transform.e)))
    return abs(dx), abs(dy)


def _normalise(data: np.ndarray) -> np.ndarray:
    source = data.astype(np.float32, copy=False)
    if data.dtype == np.uint8:
        source = source / 255.0
    else:
        source = source / 10000.0
    source = np.clip(source, 0.0, 1.0)
    return (source - MEAN) / STD


def _positions(width: int, height: int) -> list[tuple[int, int, int, int]]:
    return [
        (x, y, min(PATCH_SIZE, width - x), min(PATCH_SIZE, height - y))
        for y in range(0, height, PATCH_SIZE)
        for x in range(0, width, PATCH_SIZE)
    ]


def _area_m2(geometry: object, source_crs: str) -> float:
    transformer = Transformer.from_crs(source_crs, "EPSG:6933", always_xy=True)
    projected = shapely_transform(transformer.transform, geometry)
    return abs(float(projected.area))


def _predict_mask(
    dataset: rasterio.io.DatasetReader,
    *,
    model: SegformerForSemanticSegmentation,
    device: torch.device,
    batch_size: int,
) -> tuple[np.ndarray, dict[int, float], dict[int, int]]:
    prediction = np.zeros((dataset.height, dataset.width), dtype=np.uint8)
    confidence_sums = np.zeros(len(CLASS_CODES), dtype=np.float64)
    confidence_counts = np.zeros(len(CLASS_CODES), dtype=np.int64)
    positions = _positions(dataset.width, dataset.height)

    for start in range(0, len(positions), batch_size):
        batch_positions = positions[start : start + batch_size]
        tensors: list[np.ndarray] = []
        valid_masks: list[np.ndarray] = []
        for x, y, width, height in batch_positions:
            window = Window(x, y, width, height)
            data = dataset.read((1, 2, 3, 4), window=window)
            valid = dataset.dataset_mask(window=window) > 0
            valid &= np.any(data != 0, axis=0)
            padded = np.zeros((4, PATCH_SIZE, PATCH_SIZE), dtype=data.dtype)
            padded[:, :height, :width] = data
            tensors.append(_normalise(padded))
            valid_masks.append(valid)

        inputs = torch.from_numpy(np.stack(tensors)).to(device, non_blocking=device.type == "cuda")
        with torch.inference_mode(), torch.amp.autocast(device_type=device.type, enabled=device.type == "cuda"):
            logits = model(pixel_values=inputs).logits
            logits = F.interpolate(logits, size=(PATCH_SIZE, PATCH_SIZE), mode="bilinear", align_corners=False)
            probabilities = torch.softmax(logits, dim=1)
            confidence, class_index = probabilities.max(dim=1)
        confidence_np = confidence.float().cpu().numpy()
        index_np = class_index.cpu().numpy()

        for item, (x, y, width, height) in enumerate(batch_positions):
            indices = index_np[item, :height, :width]
            valid = valid_masks[item]
            codes = np.asarray(CLASS_CODES, dtype=np.uint8)[indices]
            codes = codes.copy()
            codes[~valid] = 0
            prediction[y : y + height, x : x + width] = codes
            local_confidence = confidence_np[item, :height, :width]
            for class_index_value in range(len(CLASS_CODES)):
                class_pixels = valid & (indices == class_index_value)
                if class_pixels.any():
                    confidence_sums[class_index_value] += float(local_confidence[class_pixels].sum())
                    confidence_counts[class_index_value] += int(class_pixels.sum())

    class_confidence = {
        CLASS_CODES[index]: float(confidence_sums[index] / confidence_counts[index])
        for index in range(len(CLASS_CODES))
        if confidence_counts[index] > 0
    }
    class_counts = {
        CLASS_CODES[index]: int(confidence_counts[index])
        for index in range(len(CLASS_CODES))
        if confidence_counts[index] > 0
    }
    return prediction, class_confidence, class_counts
def infer_and_vectorize_geotiff(
    source_path: str | Path,
    *,
    model_dir: str | Path,
    device: str = "auto",
    batch_size: int = 2,
    min_area_m2: float = 10000.0,
    min_gsd_m: float = 5.0,
    max_gsd_m: float = 20.0,
    simplify_pixels: float = 0.5,
) -> LULCResult:
    """Classify RGB+NIR imagery and dissolve each predicted WorldCover class."""
    source_path = Path(source_path)
    model_dir = Path(model_dir)
    if not model_dir.is_dir():
        raise FileNotFoundError(f"LULC model directory is unavailable: {model_dir}")

    selected_device, _ = select_device(device)
    model = SegformerForSemanticSegmentation.from_pretrained(
        str(model_dir),
        local_files_only=True,
    ).to(selected_device).eval()

    processed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with rasterio.open(source_path) as dataset:
        if dataset.crs is None:
            raise ValueError("LULC inference requires a georeferenced GeoTIFF with a CRS.")
        if dataset.count < 4:
            raise ValueError(
                "LULC inference requires four bands in RGB+NIR order. "
                f"This imagery has {dataset.count} band(s)."
            )

        gsd_x_m, gsd_y_m = _pixel_size_m(dataset)
        gsd_m = (gsd_x_m + gsd_y_m) / 2.0
        if not (min_gsd_m <= gsd_m <= max_gsd_m):
            raise ValueError(
                "This LULC model was trained on approximately 10 m Sentinel-2 RGB+NIR imagery. "
                f"Registered imagery is approximately {gsd_m:.2f} m/pixel; supported range is "
                f"{min_gsd_m:.1f}-{max_gsd_m:.1f} m/pixel."
            )

        mask, class_confidence, class_counts = _predict_mask(
            dataset,
            model=model,
            device=selected_device,
            batch_size=max(1, batch_size),
        )
        valid = mask != 0
        pixel_area_m2 = max(gsd_x_m * gsd_y_m, 1e-6)
        min_pixels = max(1, int(math.ceil(min_area_m2 / pixel_area_m2)))
        filtered = sieve(mask, size=min_pixels, mask=valid, connectivity=8)

        geometries: dict[int, list[object]] = {code: [] for code in CLASS_CODES}
        for geometry_mapping, value in shapes(
            filtered.astype(np.uint8, copy=False),
            mask=valid,
            transform=dataset.transform,
            connectivity=8,
        ):
            code = int(value)
            if code in geometries:
                geometries[code].append(shape(geometry_mapping))

        simplify_tolerance = simplify_pixels * max(
            abs(float(dataset.transform.a)),
            abs(float(dataset.transform.e)),
        )
        features: list[LULCFeature] = []
        for code in CLASS_CODES:
            candidates = geometries[code]
            if not candidates:
                continue
            merged = unary_union(candidates)
            if simplify_tolerance > 0:
                merged = merged.simplify(simplify_tolerance, preserve_topology=True)
            if merged.is_empty:
                continue
            area_m2 = _area_m2(merged, dataset.crs.to_string())
            if area_m2 < min_area_m2:
                continue
            features.append(
                LULCFeature(
                    geometry=merged,
                    land_use_class=CLASS_NAMES[code],
                    confidence=class_confidence.get(code),
                    area_m2=area_m2,
                    area_sqft=area_m2 * 10.7639104167,
                    model_version=MODEL_VERSION,
                    processed_at=processed_at,
                )
            )

        valid_pixel_count = int(sum(class_counts.values()))
        class_distribution = [
            {
                "code": code,
                "class_name": CLASS_NAMES[code],
                "pixel_count": class_counts.get(code, 0),
                "proportion": (
                    float(class_counts.get(code, 0) / valid_pixel_count)
                    if valid_pixel_count > 0
                    else 0.0
                ),
            }
            for code in CLASS_CODES
            if class_counts.get(code, 0) > 0
        ]
        parameters = {
            "engine": "segformer-b2",
            "model_version": MODEL_VERSION,
            "classes": len(CLASS_CODES),
            "input_bands": "RGBNIR",
            "patch_size": PATCH_SIZE,
            "batch_size": max(1, batch_size),
            "gsd_m": gsd_m,
            "min_area_m2": min_area_m2,
            "min_component_pixels": min_pixels,
            "feature_count": len(features),
            "valid_pixel_count": valid_pixel_count,
            "class_distribution": class_distribution,
        }
        return LULCResult(
            features=tuple(features),
            source_crs=dataset.crs.to_string(),
            processing_parameters=parameters,
        )

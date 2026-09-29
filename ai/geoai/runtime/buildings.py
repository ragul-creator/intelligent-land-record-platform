"""Real C.2 inference plus C.3 vectorization for registered GeoTIFF imagery."""

from __future__ import annotations

from collections import deque
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import rasterio
import torch
from rasterio.enums import MaskFlags
from rasterio.features import geometry_mask
from torchvision.transforms import functional as transforms

from ai.geoai.polygonization.buildings import (
    VectorizationConfig,
    VectorizationResult,
    vectorize_buildings,
)
from ai.geoai.segmentation.model import load_checkpoint
from ai.geoai.segmentation.runtime import select_device
from ai.geoai.tiling.raster_tiler import tile_geotiff


# Keep model-scale imagery on the simple direct path. Medium rasters are
# inferred as overlapping 256px tiles and stitched before vectorization so
# probability normalization matches training scale without seam duplicates.
DIRECT_INFERENCE_MAX_DIM = 256
DIRECT_INFERENCE_MAX_PIXELS = DIRECT_INFERENCE_MAX_DIM * DIRECT_INFERENCE_MAX_DIM
MOSAIC_INFERENCE_MAX_PIXELS = 2048 * 2048
MODEL_TILE_SIZE = 256
MODEL_TILE_STRIDE = 197
TILE_SIZE = 256
SATURATED_BACKGROUND_MIN = 250
MIN_BUILDING_AREA_M2 = float(os.environ.get("GEOAI_BUILDING_MIN_AREA_M2", "0.25"))
DEFAULT_BUILDING_THRESHOLD = float(os.environ.get("GEOAI_BUILDING_THRESHOLD", "0.5"))
MOSAIC_BUILDING_THRESHOLD = float(os.environ.get("GEOAI_BUILDING_MOSAIC_THRESHOLD", "0.50"))
BLUE_IRREGULAR_FILTER_ENABLED = os.environ.get("GEOAI_BUILDING_BLUE_IRREGULAR_FILTER", "0").lower() in {"1", "true", "yes"}
BLUE_EXCESS_THRESHOLD = float(os.environ.get("GEOAI_BUILDING_BLUE_EXCESS_THRESHOLD", "0.10"))
BLUE_RECTANGULARITY_THRESHOLD = float(os.environ.get("GEOAI_BUILDING_BLUE_RECTANGULARITY_THRESHOLD", "0.70"))
STRONG_BLUE_EXCESS_THRESHOLD = float(os.environ.get("GEOAI_BUILDING_STRONG_BLUE_EXCESS_THRESHOLD", "0.13"))
STRONG_BLUE_MIN_CHANNEL = float(os.environ.get("GEOAI_BUILDING_STRONG_BLUE_MIN_CHANNEL", "0.48"))
STRONG_BLUE_MAX_ASPECT = float(os.environ.get("GEOAI_BUILDING_STRONG_BLUE_MAX_ASPECT", "2.0"))
STRONG_BLUE_MAX_AREA_M2 = float(os.environ.get("GEOAI_BUILDING_STRONG_BLUE_MAX_AREA_M2", "200"))
HIGHRES_CHECKPOINT = os.environ.get("GEOAI_BUILDING_HIGHRES_CHECKPOINT", "").strip()
HIGHRES_WATER_CONTEXT_FILTER_ENABLED = os.environ.get("GEOAI_BUILDING_HIGHRES_WATER_FILTER", "1").lower() in {"1", "true", "yes"}
HIGHRES_WATER_RING_M = float(os.environ.get("GEOAI_BUILDING_HIGHRES_WATER_RING_M", "2.0"))
HIGHRES_WATER_DARK_VALUE = float(os.environ.get("GEOAI_BUILDING_HIGHRES_WATER_DARK_VALUE", "80"))
HIGHRES_WATER_DARK_FRACTION = float(os.environ.get("GEOAI_BUILDING_HIGHRES_WATER_DARK_FRACTION", "0.95"))
HIGHRES_MAX_GSD_M = float(os.environ.get("GEOAI_BUILDING_HIGHRES_MAX_GSD_M", "0.10"))


def _input_tensor(
    dataset: rasterio.io.DatasetReader,
    *,
    data: np.ndarray | None = None,
) -> torch.Tensor:
    """Read up to three raster bands into the C.2 model's normalized RGB tensor."""
    if data is None:
        indexes = list(range(1, min(3, dataset.count) + 1))
        data = dataset.read(indexes)
    source_is_uint8 = data.dtype == np.uint8
    data = data.astype(np.float32, copy=False)

    channels: list[np.ndarray] = []
    for band in data:
        finite = np.isfinite(band)

        if dataset.nodata is not None:
            finite &= band != dataset.nodata

        if not finite.any():
            channels.append(np.zeros(band.shape, dtype=np.float32))
            continue

        low, high = np.percentile(band[finite], (2, 98))

        if high <= low:
            high = low + 1.0

        working = band
        if not source_is_uint8:
            # Prepared training imagery is first percentile-stretched into
            # uint8 PNG and then normalized again by the dataset loader.
            # Mirror that two-stage path for raw 16-bit/float GeoTIFFs.
            working = np.clip(
                (band - low) * 255.0 / (high - low), 0.0, 255.0
            ).astype(np.uint8).astype(np.float32)
            finite_working = np.isfinite(working)
            low, high = np.percentile(working[finite_working], (2, 98))
            if high <= low:
                high = low + 1.0

        normalized = np.clip(
            (working - low) / (high - low),
            0.0,
            1.0,
        ).astype(np.float32, copy=False)

        channels.append(normalized)

    while len(channels) < 3:
        channels.append(channels[-1])

    tensor = torch.from_numpy(
        np.stack(channels[:3]).astype(np.float32, copy=False)
    )

    return transforms.normalize(
        tensor,
        mean=[0.485, 0.456, 0.406],
        std=[0.229, 0.224, 0.225],
    ).unsqueeze(0)


def _edge_connected_saturated_background(data: np.ndarray) -> np.ndarray:
    """Find saturated-white exterior pixels without masking isolated white roofs."""
    if data.shape[0] < 3:
        return np.zeros(data.shape[1:], dtype=bool)

    candidate = np.all(data[:3] >= SATURATED_BACKGROUND_MIN, axis=0)
    if not candidate.any():
        return candidate
    if candidate.all():
        return candidate

    height, width = candidate.shape
    exterior = np.zeros_like(candidate)
    pending: deque[tuple[int, int]] = deque()

    def add(row: int, column: int) -> None:
        if candidate[row, column] and not exterior[row, column]:
            exterior[row, column] = True
            pending.append((row, column))

    for column in range(width):
        add(0, column)
        add(height - 1, column)
    for row in range(1, height - 1):
        add(row, 0)
        add(row, width - 1)

    while pending:
        row, column = pending.popleft()
        if row > 0:
            add(row - 1, column)
        if row + 1 < height:
            add(row + 1, column)
        if column > 0:
            add(row, column - 1)
        if column + 1 < width:
            add(row, column + 1)

    return exterior


def _valid_inference_mask(
    dataset: rasterio.io.DatasetReader,
    data: np.ndarray,
) -> np.ndarray:
    """Honor raster validity and conservatively detect unmarked white exterior."""
    valid = dataset.dataset_mask() != 0
    has_explicit_validity = dataset.nodata is not None or any(
        MaskFlags.all_valid not in flags for flags in dataset.mask_flag_enums
    )
    if has_explicit_validity:
        return valid
    return valid & ~_edge_connected_saturated_background(data)


def _normalized_rgb_for_filter(dataset: rasterio.io.DatasetReader, data: np.ndarray) -> np.ndarray | None:
    if data.shape[0] < 3:
        return None
    rgb = np.zeros((data.shape[1], data.shape[2], 3), dtype=np.float32)
    for band_index in range(3):
        band = data[band_index].astype(np.float32, copy=False)
        finite = np.isfinite(band)
        if dataset.nodata is not None:
            finite &= band != dataset.nodata
        if not finite.any():
            continue
        low, high = np.percentile(band[finite], (2, 98))
        if high <= low:
            high = low + 1.0
        rgb[:, :, band_index] = np.clip((band - low) / (high - low), 0.0, 1.0)
    return rgb


def _filter_blue_irregular_candidates(result: VectorizationResult, dataset: rasterio.io.DatasetReader, data: np.ndarray) -> VectorizationResult:
    if not BLUE_IRREGULAR_FILTER_ENABLED:
        return result
    rgb = _normalized_rgb_for_filter(dataset, data)
    if rgb is None:
        return result
    kept = []
    rejected = 0
    for feature in result.features:
        pixels = geometry_mask([feature.geometry], out_shape=(dataset.height, dataset.width), transform=dataset.transform, invert=True)
        values = rgb[pixels]
        if values.size == 0:
            kept.append(feature)
            continue
        red, green, blue = [float(value) for value in values.mean(axis=0)]
        blue_excess = blue - ((red + green) * 0.5)
        rectangle = feature.geometry.minimum_rotated_rectangle
        rectangularity = float(feature.geometry.area / rectangle.area) if rectangle.area > 0 else 0.0
        coords = list(rectangle.exterior.coords) if not rectangle.is_empty else []
        if len(coords) >= 5:
            sides = [
                float(np.hypot(coords[i + 1][0] - coords[i][0], coords[i + 1][1] - coords[i][1]))
                for i in range(4)
            ]
            aspect = max(sides) / max(min(sides), 1e-9)
        else:
            aspect = 1.0
        strong_blue_compact = (
            blue_excess > STRONG_BLUE_EXCESS_THRESHOLD
            and blue > STRONG_BLUE_MIN_CHANNEL
            and aspect < STRONG_BLUE_MAX_ASPECT
            and float(feature.area_m2 or 0.0) < STRONG_BLUE_MAX_AREA_M2
        )
        if (
            blue_excess > BLUE_EXCESS_THRESHOLD
            and rectangularity < BLUE_RECTANGULARITY_THRESHOLD
        ) or strong_blue_compact:
            rejected += 1
            continue
        kept.append(feature)
    parameters = dict(result.processing_parameters)
    parameters.update({
        "blue_irregular_filter": 1.0,
        "blue_excess_threshold": BLUE_EXCESS_THRESHOLD,
        "blue_rectangularity_threshold": BLUE_RECTANGULARITY_THRESHOLD,
        "strong_blue_excess_threshold": STRONG_BLUE_EXCESS_THRESHOLD,
        "strong_blue_min_channel": STRONG_BLUE_MIN_CHANNEL,
        "strong_blue_max_aspect": STRONG_BLUE_MAX_ASPECT,
        "strong_blue_max_area_m2": STRONG_BLUE_MAX_AREA_M2,
        "blue_irregular_rejected_count": float(rejected),
    })
    return VectorizationResult(tuple(kept), result.coordinate_space, result.source_crs, result.processed_at, parameters)


def _filter_highres_water_context(
    result: VectorizationResult,
    dataset: rasterio.io.DatasetReader,
    data: np.ndarray,
) -> VectorizationResult:
    if (
        not HIGHRES_WATER_CONTEXT_FILTER_ENABLED
        or not HIGHRES_CHECKPOINT
        or dataset.crs is None
        or dataset.crs.is_geographic
        or data.shape[0] < 3
        or data.dtype != np.uint8
    ):
        return result
    gsd = max(abs(float(dataset.transform.a)), abs(float(dataset.transform.e)))
    if not (0.0 < gsd <= HIGHRES_MAX_GSD_M):
        return result

    rgb = data[:3].transpose(1, 2, 0).astype(np.float32, copy=False)
    kept = []
    rejected = 0
    for feature in result.features:
        pixels = geometry_mask(
            [feature.geometry],
            out_shape=(dataset.height, dataset.width),
            transform=dataset.transform,
            invert=True,
        )
        candidate_values = rgb[pixels]
        ring_geometry = feature.geometry.buffer(HIGHRES_WATER_RING_M).difference(feature.geometry)
        ring_pixels = geometry_mask(
            [ring_geometry],
            out_shape=(dataset.height, dataset.width),
            transform=dataset.transform,
            invert=True,
        )
        ring_values = rgb[ring_pixels]
        if candidate_values.size == 0 or ring_values.shape[0] < 64:
            kept.append(feature)
            continue
        candidate_mean = float(candidate_values.mean())
        ring_gray = ring_values.mean(axis=1)
        ring_mean = float(ring_gray.mean())
        dark_fraction = float((ring_gray < HIGHRES_WATER_DARK_VALUE).mean())
        if (
            candidate_mean < HIGHRES_WATER_DARK_VALUE
            and ring_mean < HIGHRES_WATER_DARK_VALUE
            and dark_fraction >= HIGHRES_WATER_DARK_FRACTION
        ):
            rejected += 1
            continue
        kept.append(feature)

    parameters = dict(result.processing_parameters)
    parameters.update({
        "highres_water_context_filter": 1.0,
        "highres_water_ring_m": HIGHRES_WATER_RING_M,
        "highres_water_dark_value": HIGHRES_WATER_DARK_VALUE,
        "highres_water_dark_fraction": HIGHRES_WATER_DARK_FRACTION,
        "highres_water_rejected_count": float(rejected),
    })
    return VectorizationResult(
        tuple(kept),
        result.coordinate_space,
        result.source_crs,
        result.processed_at,
        parameters,
    )


def _infer_dataset(
    dataset: rasterio.io.DatasetReader,
    *,
    model,
    selected_device: torch.device,
    source_image: str | None,
    threshold: float,
) -> VectorizationResult:
    if dataset.crs is None:
        raise ValueError(
            "Building inference requires a source GeoTIFF with a CRS."
        )

    indexes = list(range(1, min(3, dataset.count) + 1))
    data = dataset.read(indexes)
    valid = _valid_inference_mask(dataset, data)
    tensor = _input_tensor(dataset, data=data).to(
        selected_device,
        non_blocking=selected_device.type == "cuda",
    )

    with torch.inference_mode(), torch.amp.autocast(
        device_type=selected_device.type,
        enabled=selected_device.type == "cuda",
    ):
        probability = (
            model.probabilities(tensor)[0, 0]
            .float()
            .cpu()
            .numpy()
        )

    # Invalid/exterior pixels must never become vector candidates even when the
    # model assigns them high building probability.
    probability[~valid] = 0.0

    result = vectorize_buildings(
        probability,
        transform=dataset.transform,
        crs=dataset.crs,
        probability_mask=probability,
        config=VectorizationConfig(
            threshold=threshold,
            min_area=MIN_BUILDING_AREA_M2,
        ),
        model_version=model.config.model_version,
        source_image=source_image,
        source_mask=None,
    )
    result = _filter_blue_irregular_candidates(result, dataset, data)
    return _filter_highres_water_context(result, dataset, data)


def _checkpoint_for_dataset(dataset: rasterio.io.DatasetReader, checkpoint: str | Path) -> str | Path:
    """Use the hard-negative model only for projected very-high-resolution imagery."""
    if not HIGHRES_CHECKPOINT or dataset.crs is None or dataset.crs.is_geographic:
        return checkpoint
    gsd = max(abs(float(dataset.transform.a)), abs(float(dataset.transform.e)))
    if 0.0 < gsd <= HIGHRES_MAX_GSD_M:
        return HIGHRES_CHECKPOINT
    return checkpoint


def _write_parent_stretched_uint8(source_path: Path, output_path: Path) -> None:
    """Mirror training's whole-parent 2/98 stretch before crop/tile normalization."""
    with rasterio.open(source_path) as dataset:
        indexes = list(range(1, min(3, dataset.count) + 1))
        data = dataset.read(indexes)
        stretched = np.zeros(data.shape, dtype=np.uint8)
        for band_index, band in enumerate(data):
            values = band.astype(np.float32, copy=False)
            finite = np.isfinite(values)
            if dataset.nodata is not None:
                finite &= values != dataset.nodata
            if not finite.any():
                continue
            low, high = np.percentile(values[finite], (2, 98))
            if high <= low:
                high = low + 1.0
            stretched[band_index] = np.clip(
                (values - low) * 255.0 / (high - low), 0.0, 255.0
            ).astype(np.uint8)
        profile = dataset.profile.copy()
        profile.update(dtype="uint8", count=len(indexes), nodata=None)
        with rasterio.open(output_path, "w", **profile) as output:
            output.write(stretched)


def _model_tile_origins(length: int) -> list[int]:
    if length <= MODEL_TILE_SIZE:
        return [0]
    origins = list(range(0, length - MODEL_TILE_SIZE + 1, MODEL_TILE_STRIDE))
    last = length - MODEL_TILE_SIZE
    if origins[-1] != last:
        origins.append(last)
    return origins


def _infer_mosaic_dataset(
    dataset: rasterio.io.DatasetReader,
    *,
    model,
    selected_device: torch.device,
    source_image: str | None,
    threshold: float,
) -> VectorizationResult:
    indexes = list(range(1, min(3, dataset.count) + 1))
    full_data = dataset.read(indexes)
    probability_sum = np.zeros((dataset.height, dataset.width), dtype=np.float32)
    probability_weight = np.zeros((dataset.height, dataset.width), dtype=np.uint16)

    for row in _model_tile_origins(dataset.height):
        for column in _model_tile_origins(dataset.width):
            window = rasterio.windows.Window(column, row, MODEL_TILE_SIZE, MODEL_TILE_SIZE)
            data = dataset.read(indexes, window=window)
            tensor = _input_tensor(dataset, data=data).to(
                selected_device, non_blocking=selected_device.type == "cuda"
            )
            with torch.inference_mode(), torch.amp.autocast(
                device_type=selected_device.type,
                enabled=selected_device.type == "cuda",
            ):
                tile_probability = model.probabilities(tensor)[0, 0].float().cpu().numpy()
            height, width = tile_probability.shape
            probability_sum[row:row + height, column:column + width] += tile_probability
            probability_weight[row:row + height, column:column + width] += 1

    probability = probability_sum / np.maximum(probability_weight, 1)
    probability[~_valid_inference_mask(dataset, full_data)] = 0.0
    result = vectorize_buildings(
        probability,
        transform=dataset.transform,
        crs=dataset.crs,
        probability_mask=probability,
        config=VectorizationConfig(threshold=threshold, min_area=MIN_BUILDING_AREA_M2),
        model_version=model.config.model_version,
        source_image=source_image,
        source_mask=None,
    )
    result = _filter_blue_irregular_candidates(result, dataset, full_data)
    return _filter_highres_water_context(result, dataset, full_data)


def infer_and_vectorize_geotiff(
    source_path: str | Path,
    *,
    checkpoint: str | Path,
    device: str = "auto",
    source_image: str | None = None,
    threshold: float | None = None,
) -> VectorizationResult:
    """
    Run building inference while preserving source CRS/affine coordinates.

    Small rasters use direct inference. Large orthomosaics are split into
    deterministic CRS-preserving tiles so memory use remains bounded.
    """
    source_path = Path(source_path)
    threshold_is_default = threshold is None
    threshold = DEFAULT_BUILDING_THRESHOLD if threshold_is_default else threshold

    selected_device, _ = select_device(device)

    with rasterio.open(source_path) as dataset:
        if dataset.crs is None:
            raise ValueError(
                "Building inference requires a source GeoTIFF with a CRS."
            )

        selected_checkpoint = _checkpoint_for_dataset(dataset, checkpoint)
        pixel_count = dataset.width * dataset.height

    model, _ = load_checkpoint(
        selected_checkpoint,
        device=selected_device,
    )

    with rasterio.open(source_path) as dataset:
        pixel_count = dataset.width * dataset.height

        if pixel_count <= DIRECT_INFERENCE_MAX_PIXELS:
            return _infer_dataset(
                dataset,
                model=model,
                selected_device=selected_device,
                source_image=source_image,
                threshold=threshold,
            )

    if pixel_count <= MOSAIC_INFERENCE_MAX_PIXELS:
        with TemporaryDirectory(prefix="geoai-building-mosaic-") as temporary:
            mosaic_source = source_path
            with rasterio.open(source_path) as dataset:
                medium_non_uint8 = any(dtype != "uint8" for dtype in dataset.dtypes[:3])
            if medium_non_uint8:
                mosaic_source = Path(temporary) / "parent_stretched_uint8.tif"
                _write_parent_stretched_uint8(source_path, mosaic_source)
            with rasterio.open(mosaic_source) as dataset:
                return _infer_mosaic_dataset(
                    dataset,
                    model=model,
                    selected_device=selected_device,
                    source_image=source_image,
                    threshold=MOSAIC_BUILDING_THRESHOLD if threshold_is_default else threshold,
                )

    # Very large orthomosaics stay on the bounded-memory tiled path.
    with TemporaryDirectory(prefix="geoai-building-tiles-") as temporary:
        summary = tile_geotiff(
            source_path,
            tile_size=TILE_SIZE,
            output_directory=temporary,
            overwrite=True,
        )

        features = []
        source_crs: str | None = None
        processed_at: str | None = None

        for tile in summary.tiles:
            with rasterio.open(tile.path) as tile_dataset:
                result = _infer_dataset(
                    tile_dataset,
                    model=model,
                    selected_device=selected_device,
                    source_image=source_image,
                    threshold=threshold,
                )

            features.extend(result.features)

            if source_crs is None:
                source_crs = result.source_crs

            processed_at = result.processed_at

        if processed_at is None:
            raise RuntimeError(
                "Tiled building inference produced no tile results."
            )

        return VectorizationResult(
            features=tuple(features),
            coordinate_space="WORLD",
            source_crs=source_crs,
            processed_at=processed_at,
            processing_parameters={
                "threshold": threshold,
                "min_area": MIN_BUILDING_AREA_M2,
                "simplify_tolerance": 0.0,
                "default_confidence": None,
                "tile_size": float(TILE_SIZE),
                "tile_count": float(len(summary.tiles)),
            },
        )

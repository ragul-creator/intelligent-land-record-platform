"""Real C.2 inference plus C.3 vectorization for registered GeoTIFF imagery."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import rasterio
import torch
from torchvision.transforms import functional as transforms

from ai.geoai.polygonization.buildings import (
    VectorizationConfig,
    VectorizationResult,
    vectorize_buildings,
)
from ai.geoai.segmentation.model import load_checkpoint
from ai.geoai.segmentation.runtime import select_device
from ai.geoai.tiling.raster_tiler import tile_geotiff


# Keep small imagery on the simple direct path.
# Larger orthomosaics are tiled so inference memory remains bounded.
DIRECT_INFERENCE_MAX_PIXELS = 2048 * 2048
TILE_SIZE = 512


def _input_tensor(dataset: rasterio.io.DatasetReader) -> torch.Tensor:
    """Read up to three raster bands into the C.2 model's normalized RGB tensor."""
    indexes = list(range(1, min(3, dataset.count) + 1))
    data = dataset.read(indexes).astype(np.float32)

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

        normalized = np.clip(
            (band - low) / (high - low),
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

    tensor = _input_tensor(dataset).to(
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

    return vectorize_buildings(
        probability,
        transform=dataset.transform,
        crs=dataset.crs,
        probability_mask=probability,
        config=VectorizationConfig(threshold=threshold),
        model_version=model.config.model_version,
        source_image=source_image,
        source_mask=None,
    )


def infer_and_vectorize_geotiff(
    source_path: str | Path,
    *,
    checkpoint: str | Path,
    device: str = "auto",
    source_image: str | None = None,
    threshold: float = 0.5,
) -> VectorizationResult:
    """
    Run building inference while preserving source CRS/affine coordinates.

    Small rasters use direct inference. Large orthomosaics are split into
    deterministic CRS-preserving tiles so memory use remains bounded.
    """
    source_path = Path(source_path)

    selected_device, _ = select_device(device)
    model, _ = load_checkpoint(
        checkpoint,
        device=selected_device,
    )

    with rasterio.open(source_path) as dataset:
        if dataset.crs is None:
            raise ValueError(
                "Building inference requires a source GeoTIFF with a CRS."
            )

        pixel_count = dataset.width * dataset.height

        if pixel_count <= DIRECT_INFERENCE_MAX_PIXELS:
            return _infer_dataset(
                dataset,
                model=model,
                selected_device=selected_device,
                source_image=source_image,
                threshold=threshold,
            )

    # The model is intentionally loaded once above and reused across tiles.
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
                "min_area": 0.0,
                "simplify_tolerance": 0.0,
                "default_confidence": None,
                "tile_size": float(TILE_SIZE),
                "tile_count": float(len(summary.tiles)),
            },
        )

"""H.2B.5 registered-GeoTIFF road inference with one checkpoint load per job."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import rasterio
import torch

from ai.geoai.roads.model import load_checkpoint
from ai.geoai.roads.vectorization import RoadVectorizationConfig, RoadVectorizationResult, vectorize_roads
from ai.geoai.runtime.buildings import (
    DIRECT_INFERENCE_MAX_PIXELS,
    TILE_SIZE,
    _input_tensor,
    _valid_inference_mask,
)
from ai.geoai.segmentation.runtime import select_device
from ai.geoai.tiling.raster_tiler import tile_geotiff


def _infer_dataset(dataset: rasterio.io.DatasetReader, *, model, device: torch.device, threshold: float, min_component_pixels: int, simplify_tolerance: float) -> RoadVectorizationResult:
    if dataset.crs is None:
        raise ValueError("Road inference requires a source GeoTIFF with a CRS.")
    indexes = list(range(1, min(3, dataset.count) + 1))
    data = dataset.read(indexes)
    valid = _valid_inference_mask(dataset, data)
    tensor = _input_tensor(dataset, data=data).to(
        device, non_blocking=device.type == "cuda"
    )
    with torch.inference_mode(), torch.amp.autocast(
        device_type=device.type, enabled=device.type == "cuda"
    ):
        probability = (
            model.probabilities(tensor)[0, 0].float().cpu().numpy()
        )
    # Match building inference validity handling so unmarked white orthomosaic
    # exterior cannot become a road candidate either.
    probability[~valid] = float("nan")
    return vectorize_roads(probability, transform=dataset.transform, crs=dataset.crs, model_version=model.config.model_version, config=RoadVectorizationConfig(threshold=threshold, min_component_pixels=min_component_pixels, simplify_tolerance=simplify_tolerance))


def infer_and_vectorize_geotiff(source_path: str | Path, *, checkpoint: str | Path, device: str = "auto", threshold: float = 0.5, min_component_pixels: int = 16, simplify_tolerance: float = 0.0) -> RoadVectorizationResult:
    """Produce CRS-preserving road centerlines; the model is loaded exactly once."""
    source_path = Path(source_path)
    selected_device, _ = select_device(device)
    model, _ = load_checkpoint(checkpoint, device=selected_device)
    with rasterio.open(source_path) as dataset:
        if dataset.crs is None:
            raise ValueError("Road inference requires a source GeoTIFF with a CRS.")
        if dataset.width * dataset.height <= DIRECT_INFERENCE_MAX_PIXELS:
            return _infer_dataset(dataset, model=model, device=selected_device, threshold=threshold, min_component_pixels=min_component_pixels, simplify_tolerance=simplify_tolerance)
    with TemporaryDirectory(prefix="geoai-road-tiles-") as temporary:
        summary = tile_geotiff(source_path, tile_size=TILE_SIZE, output_directory=temporary, overwrite=True)
        features = []
        source_crs = None
        processed_at = None
        for tile in summary.tiles:
            with rasterio.open(tile.path) as dataset:
                result = _infer_dataset(dataset, model=model, device=selected_device, threshold=threshold, min_component_pixels=min_component_pixels, simplify_tolerance=simplify_tolerance)
            features.extend(result.features)
            source_crs, processed_at = source_crs or result.source_crs, result.processed_at
        if source_crs is None or processed_at is None:
            raise RuntimeError("Tiled road inference produced no tile results.")
        return RoadVectorizationResult(tuple(features), source_crs, processed_at, {"threshold": threshold, "min_component_pixels": float(min_component_pixels), "simplify_tolerance": simplify_tolerance, "tile_size": float(TILE_SIZE), "tile_count": float(len(summary.tiles))})

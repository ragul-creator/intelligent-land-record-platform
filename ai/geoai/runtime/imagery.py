"""Private GeoTIFF inspection and preview helpers for browser GIS use."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from pyproj import CRS, Transformer

from ai.geoai.ingestion.raster import inspect_raster


def _preview_band(array: np.ndarray, nodata: float | int | None) -> np.ndarray:
    """Scale one source band into an 8-bit display channel without changing the source."""
    values = array.astype(np.float32, copy=False)
    valid = np.isfinite(values)
    if nodata is not None:
        valid &= values != nodata
    if not valid.any():
        return np.zeros(values.shape, dtype=np.uint8)
    low, high = np.percentile(values[valid], (2, 98))
    if high <= low:
        high = low + 1.0
    return np.clip((values - low) * 255.0 / (high - low), 0, 255).astype(np.uint8)


def _preview_png(path: Path, *, maximum_dimension: int = 2048) -> bytes:
    with rasterio.open(path) as dataset:
        scale = min(1.0, maximum_dimension / max(dataset.width, dataset.height))
        out_height = max(1, round(dataset.height * scale))
        out_width = max(1, round(dataset.width * scale))
        indexes = list(range(1, min(3, dataset.count) + 1))
        data = dataset.read(indexes, out_shape=(len(indexes), out_height, out_width))
        channels = [_preview_band(band, dataset.nodata) for band in data]
        while len(channels) < 3:
            channels.append(channels[-1])
        image = Image.fromarray(np.dstack(channels[:3]), mode="RGB")
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()


def _wgs84_corners(path: Path) -> list[list[float]]:
    """Return [lon, lat] image corners from the actual source affine transform."""
    with rasterio.open(path) as dataset:
        if dataset.crs is None:
            raise ValueError("A source CRS is required for an imagery preview.")
        transformer = Transformer.from_crs(CRS.from_user_input(dataset.crs), "EPSG:4326", always_xy=True)
        pixels = ((0, 0), (dataset.width, 0), (dataset.width, dataset.height), (0, dataset.height))
        return [list(transformer.transform(*(dataset.transform @ pixel))) for pixel in pixels]


def inspect_and_render_preview(path: str | Path) -> tuple[dict[str, object], bytes, list[list[float]]]:
    """Validate a GeoTIFF, preserve its metadata, and make one derived private PNG preview."""
    source = Path(path)
    metadata = inspect_raster(source, require_crs=True).to_dict()
    with rasterio.open(source) as dataset:
        crs = CRS.from_user_input(dataset.crs)
        metadata["source_crs"] = f"EPSG:{crs.to_epsg()}" if crs.to_epsg() else crs.to_wkt()
    return metadata, _preview_png(source), _wgs84_corners(source)

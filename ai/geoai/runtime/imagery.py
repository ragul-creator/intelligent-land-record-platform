"""Private GeoTIFF inspection and browser-preview helpers for Web-GIS use."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from pyproj import CRS, Transformer
from rasterio.transform import array_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject

from ai.geoai.ingestion.raster import inspect_raster


WEB_MERCATOR = "EPSG:3857"
WGS84 = "EPSG:4326"


def _preview_band(array: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Scale one warped source band into an 8-bit display channel."""
    values = array.astype(np.float32, copy=False)
    usable = valid & np.isfinite(values)
    if not usable.any():
        return np.zeros(values.shape, dtype=np.uint8)
    low, high = np.percentile(values[usable], (2, 98))
    if high <= low:
        high = low + 1.0
    scaled = np.clip((values - low) * 255.0 / (high - low), 0, 255)
    scaled[~usable] = 0
    return scaled.astype(np.uint8)


def _preview_geometry(
    dataset: rasterio.io.DatasetReader,
    *,
    maximum_dimension: int,
) -> tuple[Affine, int, int]:
    """Return a north-up Web Mercator transform and bounded preview dimensions."""
    transform, width, height = calculate_default_transform(
        dataset.crs,
        WEB_MERCATOR,
        dataset.width,
        dataset.height,
        *dataset.bounds,
    )
    scale = min(1.0, maximum_dimension / max(width, height))
    out_width = max(1, round(width * scale))
    out_height = max(1, round(height * scale))
    if out_width != width or out_height != height:
        transform = transform * Affine.scale(width / out_width, height / out_height)
    return transform, out_width, out_height


def _wgs84_corners_from_preview(transform: Affine, width: int, height: int) -> list[list[float]]:
    """Return MapLibre image-source corners for a north-up Web Mercator preview."""
    west, south, east, north = array_bounds(height, width, transform)
    transformer = Transformer.from_crs(WEB_MERCATOR, WGS84, always_xy=True)
    projected_corners = ((west, north), (east, north), (east, south), (west, south))
    return [list(transformer.transform(x, y)) for x, y in projected_corners]


def _preview_png(
    path: Path,
    *,
    maximum_dimension: int = 2048,
) -> tuple[bytes, list[list[float]], int, int]:
    """Warp a GeoTIFF to Web Mercator and render an RGBA PNG with transparent NoData."""
    with rasterio.open(path) as dataset:
        transform, out_width, out_height = _preview_geometry(
            dataset,
            maximum_dimension=maximum_dimension,
        )

        source_mask = dataset.dataset_mask()
        alpha = np.zeros((out_height, out_width), dtype=np.uint8)
        reproject(
            source=source_mask,
            destination=alpha,
            src_transform=dataset.transform,
            src_crs=dataset.crs,
            dst_transform=transform,
            dst_crs=WEB_MERCATOR,
            src_nodata=0,
            dst_nodata=0,
            resampling=Resampling.nearest,
        )
        valid = alpha > 0

        indexes = list(range(1, min(3, dataset.count) + 1))
        channels: list[np.ndarray] = []
        for index in indexes:
            warped = np.full((out_height, out_width), np.nan, dtype=np.float32)
            reproject(
                source=rasterio.band(dataset, index),
                destination=warped,
                src_transform=dataset.transform,
                src_crs=dataset.crs,
                src_nodata=dataset.nodata,
                dst_transform=transform,
                dst_crs=WEB_MERCATOR,
                dst_nodata=np.nan,
                resampling=Resampling.bilinear,
            )
            channels.append(_preview_band(warped, valid))

        while len(channels) < 3:
            channels.append(channels[-1])

        rgba = np.dstack([*channels[:3], alpha])
        image = Image.fromarray(rgba, mode="RGBA")
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        corners = _wgs84_corners_from_preview(transform, out_width, out_height)
        return output.getvalue(), corners, out_width, out_height


def inspect_and_render_preview(path: str | Path) -> tuple[dict[str, object], bytes, list[list[float]]]:
    """Validate a GeoTIFF and create a private, transparent Web Mercator preview."""
    source = Path(path)
    metadata = inspect_raster(source, require_crs=True).to_dict()
    with rasterio.open(source) as dataset:
        crs = CRS.from_user_input(dataset.crs)
        metadata["source_crs"] = f"EPSG:{crs.to_epsg()}" if crs.to_epsg() else crs.to_wkt()

    preview, corners, preview_width, preview_height = _preview_png(source)
    metadata.update(
        {
            "preview_crs": WEB_MERCATOR,
            "preview_width": preview_width,
            "preview_height": preview_height,
            "preview_has_alpha": True,
        }
    )
    return metadata, preview, corners

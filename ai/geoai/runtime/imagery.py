"""Private GeoTIFF inspection and browser-preview helpers for Web-GIS use."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from pyproj import CRS, Transformer
from rasterio.enums import MaskFlags
from rasterio.features import bounds as feature_bounds, rasterize, shapes
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


def _edge_connected_bright_background(rgb: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Detect bright, neutral preview pixels connected to the raster border.

    Some orthomosaics encode areas outside the flown footprint as ordinary white pixels
    instead of GeoTIFF NoData. Restrict the fallback to near-white, low-chroma regions
    connected to an image edge so isolated bright roofs/objects remain visible.
    """
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        return np.zeros(valid.shape, dtype=bool)

    minimum = rgb[:, :, :3].min(axis=2)
    maximum = rgb[:, :, :3].max(axis=2)
    candidate = valid & (minimum >= 248) & ((maximum - minimum) <= 6)
    if not candidate.any():
        return np.zeros(valid.shape, dtype=bool)

    height, width = candidate.shape
    edge_geometries: list[dict[str, object]] = []
    for geometry, value in shapes(
        candidate.astype(np.uint8),
        mask=candidate,
        connectivity=8,
        transform=Affine.identity(),
    ):
        if int(value) != 1:
            continue
        left, bottom, right, top = feature_bounds(geometry)
        if left <= 0 or bottom <= 0 or right >= width or top >= height:
            edge_geometries.append(geometry)

    if not edge_geometries:
        return np.zeros(valid.shape, dtype=bool)

    return rasterize(
        edge_geometries,
        out_shape=candidate.shape,
        transform=Affine.identity(),
        fill=0,
        default_value=1,
        dtype="uint8",
    ).astype(bool)


def _has_explicit_validity(dataset: rasterio.io.DatasetReader) -> bool:
    """Return whether the source declares nodata, alpha, or another real mask."""
    return dataset.nodata is not None or any(
        MaskFlags.all_valid not in flags for flags in dataset.mask_flag_enums
    )


def _source_rgb_for_background(dataset: rasterio.io.DatasetReader) -> np.ndarray:
    """Read source RGB and normalize the native sample range to uint8."""
    indexes = list(range(1, min(3, dataset.count) + 1))
    data = dataset.read(indexes)
    if data.shape[0] < 3:
        return np.zeros((dataset.height, dataset.width, data.shape[0]), dtype=np.uint8)

    if data.dtype == np.uint8:
        normalized = data
    elif np.issubdtype(data.dtype, np.integer):
        maximum = float(np.iinfo(data.dtype).max)
        normalized = np.clip(data.astype(np.float32) * (255.0 / maximum), 0, 255)
    else:
        values = data.astype(np.float32, copy=False)
        finite = values[np.isfinite(values)]
        if finite.size and finite.min() >= 0 and finite.max() <= 1:
            values = values * 255.0
        normalized = np.clip(values, 0, 255)
    return np.moveaxis(normalized.astype(np.uint8), 0, 2)


def _preview_png(
    path: Path,
    *,
    maximum_dimension: int = 2048,
) -> tuple[bytes, list[list[float]], int, int, bool]:
    """Warp a GeoTIFF to Web Mercator and render an RGBA PNG with transparent background."""
    with rasterio.open(path) as dataset:
        transform, out_width, out_height = _preview_geometry(
            dataset,
            maximum_dimension=maximum_dimension,
        )

        source_mask = dataset.dataset_mask()
        has_explicit_validity = _has_explicit_validity(dataset)
        edge_background_removed = False
        if not has_explicit_validity:
            source_edge_background = _edge_connected_bright_background(
                _source_rgb_for_background(dataset),
                source_mask > 0,
            )
            if source_edge_background.any():
                source_mask[source_edge_background] = 0
                edge_background_removed = True
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

        rgb = np.dstack(channels[:3])
        rgba = np.dstack([rgb, alpha])
        image = Image.fromarray(rgba, mode="RGBA")
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        corners = _wgs84_corners_from_preview(transform, out_width, out_height)
        return output.getvalue(), corners, out_width, out_height, edge_background_removed


def inspect_and_render_preview(path: str | Path) -> tuple[dict[str, object], bytes, list[list[float]]]:
    """Validate a GeoTIFF and create a private, transparent Web Mercator preview."""
    source = Path(path)
    metadata = inspect_raster(source, require_crs=True).to_dict()
    with rasterio.open(source) as dataset:
        crs = CRS.from_user_input(dataset.crs)
        metadata["source_crs"] = f"EPSG:{crs.to_epsg()}" if crs.to_epsg() else crs.to_wkt()

    preview, corners, preview_width, preview_height, edge_background_removed = _preview_png(source)
    metadata.update(
        {
            "preview_crs": WEB_MERCATOR,
            "preview_width": preview_width,
            "preview_height": preview_height,
            "preview_has_alpha": True,
            "preview_edge_background_removed": edge_background_removed,
        }
    )
    return metadata, preview, corners

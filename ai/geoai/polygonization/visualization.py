"""Read-only debug overlays for preliminary C.3 building GeoJSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterator

import rasterio
from PIL import Image, ImageDraw
from pyproj import CRS, Transformer


class VisualizationError(ValueError):
    """Raised when GeoJSON cannot be safely mapped onto the supplied image."""


def _rings(geometry: dict[str, object]) -> Iterator[list[list[float]]]:
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        yield from coordinates  # type: ignore[misc]
    elif geometry_type == "MultiPolygon":
        for polygon in coordinates:  # type: ignore[union-attr]
            yield from polygon
    else:
        raise VisualizationError(f"Unsupported GeoJSON geometry type for overlay: {geometry_type!r}.")


def _coordinate_space(feature: dict[str, object], collection: dict[str, object]) -> str:
    properties = feature.get("properties") or {}
    metadata = collection.get("metadata") or {}
    coordinate_space = properties.get("coordinate_space") or metadata.get("coordinate_space")
    if coordinate_space not in {"PIXEL", "WORLD"}:
        raise VisualizationError("GeoJSON feature must declare coordinate_space as PIXEL or WORLD.")
    return coordinate_space


def _world_to_pixel_mapper(
    feature: dict[str, object],
    collection: dict[str, object],
    source_raster: Path | None,
    image_size: tuple[int, int],
):
    if source_raster is None:
        raise VisualizationError("WORLD-coordinate GeoJSON requires --source-raster for affine pixel mapping.")
    properties = feature.get("properties") or {}
    metadata = collection.get("metadata") or {}
    geometry_crs = properties.get("crs") or metadata.get("export_crs")
    if geometry_crs is None:
        raise VisualizationError("WORLD-coordinate GeoJSON must declare the CRS of its geometry coordinates.")
    with rasterio.open(source_raster) as dataset:
        if dataset.crs is None or dataset.transform.is_identity or dataset.transform.determinant == 0:
            raise VisualizationError("Source raster must provide a valid CRS and non-identity affine transform.")
        if (dataset.width, dataset.height) != image_size:
            raise VisualizationError(
                f"Image dimensions {image_size[0]}x{image_size[1]} do not match source raster "
                f"{dataset.width}x{dataset.height}."
            )
        transformer = Transformer.from_crs(CRS.from_user_input(geometry_crs), dataset.crs, always_xy=True)
        inverse = ~dataset.transform

    def world_to_pixel(x: float, y: float) -> tuple[float, float]:
        source_x, source_y = transformer.transform(x, y)
        return inverse @ (source_x, source_y)

    return world_to_pixel


def render_building_overlay(
    image_path: str | Path,
    geojson_path: str | Path,
    output_path: str | Path,
    *,
    source_raster: str | Path | None = None,
    draw_labels: bool = False,
) -> Path:
    """Draw C.3 feature boundaries on an image without changing the source or GeoJSON."""
    source_image = Path(image_path)
    source_geojson = Path(geojson_path)
    if not source_image.is_file() or not source_geojson.is_file():
        raise VisualizationError("Image and GeoJSON inputs must both exist.")
    collection = json.loads(source_geojson.read_text(encoding="utf-8"))
    if collection.get("type") != "FeatureCollection":
        raise VisualizationError("GeoJSON input must be a FeatureCollection.")
    with Image.open(source_image) as opened:
        image = opened.convert("RGBA")
    overlay = ImageDraw.Draw(image)
    outline_width = max(2, min(image.size) // 300)
    raster_path = Path(source_raster) if source_raster is not None else None
    for feature_number, feature in enumerate(collection.get("features", []), start=1):
        geometry = feature.get("geometry")
        if not geometry:
            continue
        coordinate_space = _coordinate_space(feature, collection)
        mapper = (
            _world_to_pixel_mapper(feature, collection, raster_path, image.size)
            if coordinate_space == "WORLD"
            else lambda x, y: (x, y)
        )
        first_point: tuple[float, float] | None = None
        for ring in _rings(geometry):
            points = [mapper(float(point[0]), float(point[1])) for point in ring]
            if len(points) < 2:
                continue
            first_point = first_point or points[0]
            overlay.line(points, fill=(0, 0, 0, 255), width=outline_width + 2, joint="curve")
            overlay.line(points, fill=(255, 230, 0, 255), width=outline_width, joint="curve")
        if draw_labels and first_point is not None:
            overlay.text(first_point, str(feature_number), fill=(255, 230, 0, 255), stroke_width=1, stroke_fill=(0, 0, 0, 255))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(output, format="PNG")
    return output

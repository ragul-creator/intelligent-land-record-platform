import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from PIL import Image
from rasterio.transform import from_origin

from ai.geoai.polygonization.buildings import vectorize_buildings, write_geojson
from ai.geoai.polygonization.visualization import VisualizationError, render_building_overlay


def _white_image(path: Path, size: tuple[int, int]) -> None:
    Image.new("RGB", size, "white").save(path)


def test_pixel_geojson_is_drawn_at_pixel_coordinates(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    geojson_path = tmp_path / "pixel.geojson"
    output_path = tmp_path / "overlay.png"
    _white_image(image_path, (12, 12))
    mask = np.zeros((12, 12), dtype=np.uint8)
    mask[3:8, 4:9] = 1
    write_geojson(vectorize_buildings(mask), geojson_path)

    render_building_overlay(image_path, geojson_path, output_path, draw_labels=True)

    overlay = Image.open(output_path)
    assert overlay.size == (12, 12)
    assert overlay.getpixel((4, 3)) != (255, 255, 255)


def test_world_geojson_uses_source_raster_crs_and_inverse_affine(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    raster_path = tmp_path / "source.tif"
    geojson_path = tmp_path / "world.geojson"
    output_path = tmp_path / "overlay.png"
    _white_image(image_path, (10, 10))
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=10,
        height=10,
        count=1,
        dtype="uint8",
        transform=from_origin(500000, 3000000, 2, 2),
        crs="EPSG:32618",
    ) as dataset:
        dataset.write(np.zeros((1, 10, 10), dtype=np.uint8))
    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[2:6, 3:7] = 1
    write_geojson(vectorize_buildings(mask, transform=from_origin(500000, 3000000, 2, 2), crs="EPSG:32618"), geojson_path)

    render_building_overlay(image_path, geojson_path, output_path, source_raster=raster_path)

    assert Image.open(output_path).getpixel((3, 2)) != (255, 255, 255)


def test_world_geojson_requires_source_raster(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    geojson_path = tmp_path / "world.geojson"
    _white_image(image_path, (4, 4))
    write_geojson(vectorize_buildings(np.ones((4, 4), dtype=np.uint8), transform=from_origin(0, 4, 1, 1), crs="EPSG:32618"), geojson_path)

    with pytest.raises(VisualizationError, match="requires --source-raster"):
        render_building_overlay(image_path, geojson_path, tmp_path / "overlay.png")


def test_world_geojson_rejects_nonmatching_image_and_source_raster(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    raster_path = tmp_path / "source.tif"
    geojson_path = tmp_path / "world.geojson"
    _white_image(image_path, (4, 4))
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=5,
        height=5,
        count=1,
        dtype="uint8",
        transform=from_origin(0, 5, 1, 1),
        crs="EPSG:32618",
    ) as dataset:
        dataset.write(np.zeros((1, 5, 5), dtype=np.uint8))
    write_geojson(vectorize_buildings(np.ones((4, 4), dtype=np.uint8), transform=from_origin(0, 4, 1, 1), crs="EPSG:32618"), geojson_path)

    with pytest.raises(VisualizationError, match="do not match"):
        render_building_overlay(image_path, geojson_path, tmp_path / "overlay.png", source_raster=raster_path)


def test_non_feature_collection_is_rejected(tmp_path: Path) -> None:
    image_path = tmp_path / "image.png"
    geojson_path = tmp_path / "invalid.geojson"
    _white_image(image_path, (4, 4))
    geojson_path.write_text(json.dumps({"type": "Feature"}), encoding="utf-8")

    with pytest.raises(VisualizationError, match="FeatureCollection"):
        render_building_overlay(image_path, geojson_path, tmp_path / "overlay.png")

import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.transform import from_origin
from shapely.geometry import Polygon

from ai.geoai.polygonization.buildings import VectorizationConfig, load_mask, vectorize_buildings, vectorize_from_paths, write_geojson
from ai.geoai.polygonization.geometry import SQUARE_FEET_PER_SQUARE_METRE, clean_polygon


def test_simple_binary_rectangle_creates_one_pixel_polygon() -> None:
    mask = np.zeros((8, 8), dtype=np.uint8)
    mask[2:6, 1:5] = 1

    result = vectorize_buildings(mask, model_version="unit-model", source_mask="mask.npy")

    assert result.coordinate_space == "PIXEL"
    assert len(result.features) == 1
    assert result.features[0].geometry.area == pytest.approx(16.0)
    assert result.features[0].area_m2 is None
    assert result.features[0].area_sqft is None
    assert result.features[0].confidence is None


def test_two_separate_buildings_remain_separate_with_8_connectivity() -> None:
    mask = np.zeros((8, 8), dtype=bool)
    mask[1:3, 1:3] = True
    mask[5:7, 5:7] = True

    result = vectorize_buildings(mask)

    assert len(result.features) == 2


def test_minimum_area_filters_tiny_pixel_artifact() -> None:
    mask = np.zeros((8, 8), dtype=np.uint8)
    mask[1:4, 1:4] = 255
    mask[6, 6] = 255

    result = vectorize_buildings(mask, config=VectorizationConfig(min_area=4.0))

    assert len(result.features) == 1
    assert result.features[0].geometry.area == pytest.approx(9.0)


def test_affine_world_coordinates_crs_and_projected_area_are_correct() -> None:
    mask = np.zeros((6, 6), dtype=np.uint8)
    mask[2:4, 3:5] = 1
    transform = Affine(2, 0, 100, 0, -2, 200)

    result = vectorize_buildings(mask, transform=transform, crs="EPSG:32618")
    feature = result.features[0]

    assert result.coordinate_space == "WORLD"
    assert result.source_crs == "EPSG:32618"
    assert feature.geometry.bounds == pytest.approx((106.0, 192.0, 110.0, 196.0))
    assert feature.area_m2 == pytest.approx(16.0)
    assert feature.area_sqft == pytest.approx(16.0 * SQUARE_FEET_PER_SQUARE_METRE)
    assert feature.crs == "EPSG:32618"


def test_geographic_area_uses_geodesic_measurement_not_degree_squared() -> None:
    mask = np.ones((1, 1), dtype=np.uint8)
    result = vectorize_buildings(mask, transform=Affine(0.001, 0, 77, 0, -0.001, 28), crs="EPSG:4326")

    feature = result.features[0]
    assert feature.geometry.area == pytest.approx(0.000001)
    assert feature.area_m2 is not None
    assert feature.area_m2 > 1000.0


def test_probability_confidence_uses_component_mean() -> None:
    mask = np.zeros((3, 3), dtype=np.uint8)
    probability = np.zeros((3, 3), dtype=np.float32)
    probability[1, 1:3] = [0.6, 0.8]

    result = vectorize_buildings(mask, probability_mask=probability, config=VectorizationConfig(threshold=0.5))

    assert result.features[0].confidence == pytest.approx(0.7)


def test_empty_masks_and_simplification_are_safe() -> None:
    assert vectorize_buildings(np.zeros((4, 4), dtype=np.uint8)).features == ()
    mask = np.zeros((8, 8), dtype=np.uint8)
    mask[1:7, 1:7] = 1
    mask[3:5, 3:5] = 0
    result = vectorize_buildings(mask, config=VectorizationConfig(simplify_tolerance=0.25))
    assert result.features[0].geometry.is_valid
    assert len(result.features[0].geometry.interiors) == 1


def test_invalid_polygon_repair_is_safe() -> None:
    invalid = Polygon([(0, 0), (2, 2), (2, 0), (0, 2), (0, 0)])

    repaired = clean_polygon(invalid)

    assert repaired is not None
    assert repaired.is_valid


def test_geojson_is_serializable_and_keeps_provenance() -> None:
    mask = np.ones((2, 2), dtype=np.uint8)
    result = vectorize_buildings(
        mask,
        transform=from_origin(500000, 3000000, 1, 1),
        crs="EPSG:32618",
        model_version="test-model",
        source_image="source.tif",
        source_mask="mask.npy",
    )
    collection = result.to_feature_collection()

    assert json.loads(json.dumps(collection))["type"] == "FeatureCollection"
    properties = collection["features"][0]["properties"]
    assert properties["crs"] == "EPSG:4326"
    assert properties["source_crs"] == "EPSG:32618"
    assert properties["status"] == "AI_PRELIMINARY"
    assert properties["verification_status"] == "UNVERIFIED"
    assert collection["metadata"]["processing_parameters"]["threshold"] == 0.5


def test_source_raster_dimension_mismatch_is_rejected(tmp_path: Path) -> None:
    raster_path = tmp_path / "source.tif"
    mask_path = tmp_path / "mask.npy"
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=4,
        height=4,
        count=1,
        dtype="uint8",
        transform=from_origin(0, 4, 1, 1),
        crs="EPSG:32618",
    ) as dataset:
        dataset.write(np.zeros((1, 4, 4), dtype=np.uint8))
    np.save(mask_path, np.zeros((3, 3), dtype=np.uint8))

    with pytest.raises(ValueError, match="do not match"):
        vectorize_from_paths(mask_path, source_raster=raster_path)


def test_write_geojson_and_load_mask_round_trip(tmp_path: Path) -> None:
    mask_path = tmp_path / "mask.npy"
    output_path = tmp_path / "output.geojson"
    np.save(mask_path, np.ones((2, 2), dtype=np.uint8))

    result = vectorize_from_paths(mask_path, model_version="local")
    write_geojson(result, output_path)

    assert load_mask(mask_path).shape == (2, 2)
    assert json.loads(output_path.read_text(encoding="utf-8"))["features"][0]["properties"]["model_version"] == "local"

import json

import numpy as np
import pytest
import rasterio
import torch
from affine import Affine
from rasterio.crs import CRS
from pyproj import Transformer

from ai.geoai.roads.dataset import (
    RoadSegmentationDataset,
    _normalize_rgb_bands,
    rasterize_vector_label,
    validate_road_dataset,
)
from ai.geoai.roads.model import load_checkpoint
from ai.geoai.roads.pipeline import _best_threshold, _evaluation_thresholds
from ai.geoai.roads.vectorization import RoadVectorizationConfig, vectorize_roads
from ai.geoai.segmentation.metrics import SegmentationMetrics


def test_road_mask_vectorizes_to_a_confident_projected_centerline() -> None:
    probability = np.zeros((12, 12), dtype=np.float32)
    probability[5:7, 2:10] = 0.8

    result = vectorize_roads(probability, transform=Affine.translation(500000, 1000000), crs="EPSG:32643", model_version="road-segmentation-h2b5-v1", config=RoadVectorizationConfig(
        threshold=0.5,
        min_component_pixels=8,
        min_path_pixels=2,
        closing_radius=0,
    ))

    assert len(result.features) == 1
    assert result.features[0].confidence == pytest.approx(0.8)
    assert result.features[0].length_m is not None and result.features[0].length_m > 0
    assert result.features[0].geometry.is_valid
    assert result.source_crs == "EPSG:32643"


def test_road_vectorization_drops_noise_and_refuses_missing_crs() -> None:
    probability = np.zeros((5, 5), dtype=np.float32)
    probability[0, 0] = 0.9
    assert not vectorize_roads(probability, transform=Affine.identity(), crs="EPSG:3857", model_version="road-segmentation-h2b5-v1", config=RoadVectorizationConfig(min_component_pixels=2)).features
    with pytest.raises(ValueError, match="CRS"):
        vectorize_roads(probability, transform=Affine.identity(), crs=None, model_version="road-segmentation-h2b5-v1")


def test_spacenet_style_vector_labels_are_rasterized(tmp_path) -> None:
    image_dir = tmp_path / "train" / "images"
    label_dir = tmp_path / "train" / "labels"
    image_dir.mkdir(parents=True)
    label_dir.mkdir()
    image_path = image_dir / "tile-1.tif"
    with rasterio.open(image_path, "w", driver="GTiff", width=8, height=8, count=3, dtype="uint8", crs=CRS.from_epsg(3857), transform=Affine.identity()) as dataset:
        dataset.write(np.zeros((3, 8, 8), dtype=np.uint8))
    label_path = label_dir / "tile-1.geojson"
    label_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {
                    "type": "name",
                    "properties": {"name": "EPSG:3857"},
                },
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [[1, 1], [6, 6]],
                        },
                        "properties": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    check = validate_road_dataset(tmp_path, "train")

    assert check.pair_count == 1 and check.vector_label_count == 1
    assert rasterize_vector_label(image_path, label_path).any()
    sample = RoadSegmentationDataset(tmp_path, "train")[0]
    assert sample["image"].shape == (3, 8, 8)
    assert sample["mask"].shape == (1, 8, 8)


def test_missing_road_checkpoint_fails_without_building_model_fallback(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="GEOAI_ROAD_CHECKPOINT"):
        load_checkpoint(tmp_path / "missing-road.pt", device=torch.device("cpu"))


def test_road_training_preprocessing_matches_percentile_runtime_scaling() -> None:
    base = np.arange(100, dtype=np.float32).reshape(10, 10)
    bands = np.stack((base, base + 25, base + 50))

    normalized = _normalize_rgb_bands(bands)

    assert normalized.shape == (3, 10, 10)
    assert np.all(normalized >= 0.0)
    assert np.all(normalized <= 1.0)
    assert normalized[:, 0, 0].max() == pytest.approx(0.0)
    assert normalized[:, -1, -1].min() == pytest.approx(1.0)


def test_projected_road_labels_choose_metric_buffer_from_wgs84_centre(tmp_path) -> None:
    image_path = tmp_path / "utm.tif"
    transform = Affine.translation(400000, 1450000) * Affine.scale(1, -1)
    with rasterio.open(
        image_path,
        "w",
        driver="GTiff",
        width=64,
        height=64,
        count=3,
        dtype="uint8",
        crs=CRS.from_epsg(32644),
        transform=transform,
    ) as dataset:
        dataset.write(np.zeros((3, 64, 64), dtype=np.uint8))

    label_path = tmp_path / "utm.geojson"
    label_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {
                    "type": "name",
                    "properties": {"name": "EPSG:32644"},
                },
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [
                                [400010, 1449990],
                                [400050, 1449950],
                            ],
                        },
                        "properties": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    mask = rasterize_vector_label(image_path, label_path)

    assert mask.any()


def test_crs84_spacenet_label_rasterizes_into_projected_image(tmp_path) -> None:
    image_crs = CRS.from_epsg(32643)
    to_image = Transformer.from_crs(
        "EPSG:4326", image_crs, always_xy=True
    )
    start = (72.82, 18.95)
    end = (72.8203, 18.9497)
    start_x, start_y = to_image.transform(*start)
    image_path = tmp_path / "mumbai.tif"
    transform = (
        Affine.translation(start_x - 10, start_y + 10)
        * Affine.scale(1, -1)
    )
    with rasterio.open(
        image_path,
        "w",
        driver="GTiff",
        width=80,
        height=80,
        count=3,
        dtype="uint8",
        crs=image_crs,
        transform=transform,
    ) as dataset:
        dataset.write(np.zeros((3, 80, 80), dtype=np.uint8))

    label_path = tmp_path / "mumbai.geojson"
    label_path.write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "crs": {
                    "type": "name",
                    "properties": {
                        "name": "urn:ogc:def:crs:OGC:1.3:CRS84"
                    },
                },
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "LineString",
                            "coordinates": [list(start), list(end)],
                        },
                        "properties": {},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    mask = rasterize_vector_label(image_path, label_path)

    assert mask.any()


def test_road_threshold_selection_prefers_best_validation_iou() -> None:
    sweep = {
        0.3: SegmentationMetrics(0.61, 0.76, 0.70, 0.83),
        0.4: SegmentationMetrics(0.68, 0.81, 0.78, 0.85),
        0.5: SegmentationMetrics(0.64, 0.78, 0.86, 0.72),
    }

    threshold, metrics = _best_threshold(sweep)

    assert threshold == 0.4
    assert metrics.iou == pytest.approx(0.68)

def test_road_evaluation_can_use_a_fixed_threshold() -> None:
    assert _evaluation_thresholds(None) == (0.30, 0.40, 0.50, 0.60, 0.70)
    assert _evaluation_thresholds(0.4) == (0.4,)
    with pytest.raises(ValueError, match="threshold"):
        _evaluation_thresholds(-0.01)
    with pytest.raises(ValueError, match="threshold"):
        _evaluation_thresholds(1.01)



def test_skeleton_road_vectorizer_follows_a_bend_without_diagonal_shortcut() -> None:
    probability = np.zeros((24, 24), dtype=np.float32)

    probability[5, 4:19] = 0.9
    probability[5:20, 18] = 0.9

    result = vectorize_roads(
        probability,
        transform=Affine.identity(),
        crs="EPSG:3857",
        model_version="road-segmentation-h2b5-v1",
        config=RoadVectorizationConfig(
            threshold=0.5,
            min_component_pixels=4,
            min_path_pixels=2,
            closing_radius=0,
        ),
    )

    assert len(result.features) == 1

    line = result.features[0].geometry
    coordinates = np.asarray(line.coords)

    assert len(coordinates) > 4

    direct_distance = np.linalg.norm(
        coordinates[-1] - coordinates[0]
    )

    # An L-shaped road must remain longer than a straight diagonal
    # joining its endpoints.
    assert line.length > direct_distance * 1.2


def test_skeleton_road_vectorizer_preserves_crossroad_branches() -> None:
    probability = np.zeros((25, 25), dtype=np.float32)

    probability[12, 3:22] = 0.9
    probability[3:22, 12] = 0.9

    result = vectorize_roads(
        probability,
        transform=Affine.identity(),
        crs="EPSG:3857",
        model_version="road-segmentation-h2b5-v1",
        config=RoadVectorizationConfig(
            threshold=0.5,
            min_component_pixels=4,
            min_path_pixels=3,
            closing_radius=0,
        ),
    )

    # The crossing must remain a graph with multiple road branches
    # rather than collapsing to one principal-axis line.
    assert len(result.features) >= 4

    assert all(
        feature.geometry.is_valid
        and feature.geometry.length > 0
        for feature in result.features
    )

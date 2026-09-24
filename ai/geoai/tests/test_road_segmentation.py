import json

import numpy as np
import pytest
import rasterio
import torch
from affine import Affine
from rasterio.crs import CRS

from ai.geoai.roads.dataset import (
    RoadSegmentationDataset,
    _normalize_rgb_bands,
    rasterize_vector_label,
    validate_road_dataset,
)
from ai.geoai.roads.model import load_checkpoint
from ai.geoai.roads.pipeline import _best_threshold
from ai.geoai.roads.vectorization import RoadVectorizationConfig, vectorize_roads
from ai.geoai.segmentation.metrics import SegmentationMetrics


def test_road_mask_vectorizes_to_a_confident_projected_centerline() -> None:
    probability = np.zeros((12, 12), dtype=np.float32)
    probability[5:7, 2:10] = 0.8

    result = vectorize_roads(probability, transform=Affine.translation(500000, 1000000), crs="EPSG:32643", model_version="road-segmentation-h2b5-v1", config=RoadVectorizationConfig(threshold=0.5, min_component_pixels=8))

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
    label_path.write_text(json.dumps({"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[1, 1], [6, 6]]}, "properties": {}}]}), encoding="utf-8")

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


def test_road_threshold_selection_prefers_best_validation_iou() -> None:
    sweep = {
        0.3: SegmentationMetrics(0.61, 0.76, 0.70, 0.83),
        0.4: SegmentationMetrics(0.68, 0.81, 0.78, 0.85),
        0.5: SegmentationMetrics(0.64, 0.78, 0.86, 0.72),
    }

    threshold, metrics = _best_threshold(sweep)

    assert threshold == 0.4
    assert metrics.iou == pytest.approx(0.68)

import json

import numpy as np
import pytest
import rasterio
import torch
from affine import Affine
from rasterio.crs import CRS

from ai.geoai.roads.dataset import RoadSegmentationDataset, rasterize_vector_label, validate_road_dataset
from ai.geoai.roads.model import load_checkpoint
from ai.geoai.roads.vectorization import RoadVectorizationConfig, vectorize_roads


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

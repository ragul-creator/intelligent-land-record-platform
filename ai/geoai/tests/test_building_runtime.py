"""Focused regression coverage for registered-GeoTIFF building inference."""

from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
import torch
from rasterio.transform import from_origin

from ai.geoai.runtime.buildings import _infer_dataset


class _AllBuildingModel:
    config = SimpleNamespace(model_version="building-runtime-test")

    def probabilities(self, tensor: torch.Tensor) -> torch.Tensor:
        _, _, height, width = tensor.shape
        return torch.ones((1, 1, height, width), dtype=torch.float32, device=tensor.device)


class _ComponentModel:
    config = SimpleNamespace(model_version="building-runtime-test")

    def probabilities(self, tensor: torch.Tensor) -> torch.Tensor:
        _, _, height, width = tensor.shape
        probability = torch.zeros((1, 1, height, width), dtype=torch.float32, device=tensor.device)
        probability[:, :, 2, 2] = 1.0
        probability[:, :, 20:40, 20:40] = 1.0
        return probability


def test_inference_excludes_border_connected_white_background_without_removing_interior_white_pixels(
    tmp_path,
) -> None:
    raster_path = tmp_path / "unmarked-white-background.tif"
    data = np.full((3, 32, 32), 100, dtype=np.uint8)
    data[:, :4, :] = 255
    data[:, -4:, :] = 255
    data[:, :, :4] = 255
    data[:, :, -4:] = 255
    data[:, 14:18, 14:18] = 255

    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=32,
        height=32,
        count=3,
        dtype="uint8",
        crs="EPSG:3857",
        transform=from_origin(0, 32, 1, 1),
    ) as dataset:
        dataset.write(data)

    with rasterio.open(raster_path) as dataset:
        result = _infer_dataset(
            dataset,
            model=_AllBuildingModel(),
            selected_device=torch.device("cpu"),
            source_image="test-raster",
            threshold=0.5,
        )

    assert len(result.features) == 1
    feature = result.features[0]
    assert feature.geometry.bounds == pytest.approx((4.0, 4.0, 28.0, 28.0))
    assert feature.geometry.area == pytest.approx(576.0)
    assert feature.confidence == pytest.approx(1.0)
    assert feature.model_version == "building-runtime-test"


def test_inference_filters_sub_square_metre_speckles_but_keeps_building_components(
    tmp_path,
) -> None:
    raster_path = tmp_path / "small-components.tif"
    data = np.full((3, 64, 64), 100, dtype=np.uint8)

    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=64,
        height=64,
        count=3,
        dtype="uint8",
        crs="EPSG:3857",
        transform=from_origin(0, 3.2, 0.05, 0.05),
    ) as dataset:
        dataset.write(data)

    with rasterio.open(raster_path) as dataset:
        result = _infer_dataset(
            dataset,
            model=_ComponentModel(),
            selected_device=torch.device("cpu"),
            source_image="test-raster",
            threshold=0.5,
        )

    assert len(result.features) == 1
    assert result.features[0].area_m2 == pytest.approx(1.0)

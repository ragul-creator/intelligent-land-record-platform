from pathlib import Path

import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.transform import from_origin
from rasterio.windows import Window, bounds as window_bounds, transform as window_transform

from ai.geoai.ingestion.raster import RasterValidationError, inspect_raster
from ai.geoai.tiling.raster_tiler import tile_geotiff


def write_raster(path: Path, *, width: int = 700, height: int = 600, crs: CRS | None = CRS.from_epsg(32643)) -> Affine:
    transform = from_origin(500_000, 2_000_000, 2, 3)
    profile = {
        "driver": "GTiff",
        "width": width,
        "height": height,
        "count": 2,
        "dtype": "uint16",
        "crs": crs,
        "transform": transform,
        "nodata": 65535,
    }
    data = np.arange(width * height, dtype=np.uint16).reshape(height, width)
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(data, 1)
        dataset.write(data + 1, 2)
    return transform


def test_inspect_extracts_typed_georeferenced_metadata(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    transform = write_raster(source)

    metadata = inspect_raster(source)

    assert metadata.filename == "source.tif"
    assert (metadata.driver, metadata.width, metadata.height, metadata.band_count) == ("GTiff", 700, 600, 2)
    assert metadata.epsg == 32643
    assert metadata.is_projected is True
    assert metadata.is_geographic is False
    assert metadata.nodata == 65535
    assert metadata.transform.c == transform.c
    assert metadata.estimated_uncompressed_size_bytes == 700 * 600 * 2 * 2


def test_inspect_rejects_missing_required_crs(tmp_path: Path) -> None:
    source = tmp_path / "missing-crs.tif"
    write_raster(source, crs=None)

    with pytest.raises(RasterValidationError, match="CRS is required"):
        inspect_raster(source)


def test_tiling_preserves_georeferencing_data_and_edge_dimensions(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    source_transform = write_raster(source)
    output = tmp_path / "tiles"

    summary = tile_geotiff(source, tile_size=512, output_directory=output)

    assert len(summary.tiles) == 4
    assert [tile.path.name for tile in summary.tiles] == [
        "source_r0000_c0000.tif",
        "source_r0000_c0001.tif",
        "source_r0001_c0000.tif",
        "source_r0001_c0001.tif",
    ]
    assert [(tile.width, tile.height) for tile in summary.tiles] == [(512, 512), (188, 512), (512, 88), (188, 88)]

    non_origin = summary.tiles[1]
    expected_window = Window(512, 0, 188, 512)
    expected_transform = window_transform(expected_window, source_transform)
    expected_bounds = window_bounds(expected_window, source_transform)
    assert non_origin.transform != summary.tiles[0].transform
    assert non_origin.transform.c == expected_transform.c
    assert non_origin.transform.f == expected_transform.f
    assert tuple(non_origin.bounds.__dict__.values()) == pytest.approx(expected_bounds)

    with rasterio.open(source) as source_dataset, rasterio.open(non_origin.path) as tile_dataset:
        assert (tile_dataset.width, tile_dataset.height) == (188, 512)
        assert tile_dataset.count == 2
        assert tile_dataset.dtypes == ("uint16", "uint16")
        assert tile_dataset.crs == CRS.from_epsg(32643)
        assert tile_dataset.nodata == 65535
        assert tile_dataset.transform == expected_transform
        assert np.array_equal(tile_dataset.read(), source_dataset.read(window=expected_window))

    assert min(tile.bounds.left for tile in summary.tiles) == summary.source_metadata.bounds.left
    assert max(tile.bounds.right for tile in summary.tiles) == summary.source_metadata.bounds.right
    assert min(tile.bounds.bottom for tile in summary.tiles) == summary.source_metadata.bounds.bottom
    assert max(tile.bounds.top for tile in summary.tiles) == summary.source_metadata.bounds.top


def test_tiler_does_not_overwrite_deterministic_tile_names_without_opt_in(tmp_path: Path) -> None:
    source = tmp_path / "source.tif"
    write_raster(source, width=512, height=512)
    output = tmp_path / "tiles"
    tile_geotiff(source, tile_size=512, output_directory=output)

    with pytest.raises(FileExistsError):
        tile_geotiff(source, tile_size=512, output_directory=output)

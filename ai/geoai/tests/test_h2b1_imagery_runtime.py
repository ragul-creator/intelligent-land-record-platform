from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin

from ai.geoai.runtime.imagery import inspect_and_render_preview
from ai.geoai.parcels.acquisition import create_parcel


def _write_geotiff(path: Path) -> None:
    profile = {
        "driver": "GTiff",
        "width": 8,
        "height": 6,
        "count": 3,
        "dtype": "uint8",
        "crs": "EPSG:32643",
        "transform": from_origin(500_000, 2_000_000, 2, 2),
        "nodata": 0,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        for index in range(1, 4):
            dataset.write(np.full((6, 8), 25 * index, dtype=np.uint8), index)


def test_private_preview_preserves_inspected_georeferencing_without_copying_source(tmp_path: Path) -> None:
    source = tmp_path / "registered.tif"
    _write_geotiff(source)

    metadata, preview, corners = inspect_and_render_preview(source)

    assert metadata["source_crs"] == "EPSG:32643"
    assert metadata["width"] == 8 and metadata["height"] == 6
    assert metadata["transform"]["c"] == 500_000
    assert len(corners) == 4
    assert all(-180 <= longitude <= 180 and -90 <= latitude <= 90 for longitude, latitude in corners)
    preview_path = tmp_path / "preview.png"
    preview_path.write_bytes(preview)
    with Image.open(preview_path) as image:
        assert image.mode == "RGB"
        assert image.size == (8, 6)
    with rasterio.open(source) as dataset:
        assert dataset.crs.to_epsg() == 32643
        assert dataset.transform.c == 500_000


def test_human_drawn_parcel_stays_a_draft_without_a_source_identifier() -> None:
    result = create_parcel(
        "HUMAN_DRAWN",
        {"geometry": {"type": "Polygon", "coordinates": [[[77.0, 28.0], [77.001, 28.0], [77.001, 28.001], [77.0, 28.001], [77.0, 28.0]]]}},
        source_crs="EPSG:4326",
        source_reference=None,
    )

    assert result.source.value == "HUMAN_DRAWN"
    assert result.source_reference is None
    assert result.status == "DRAFT"
    assert result.verification_status == "UNVERIFIED"
    assert result.area_m2 is not None and result.area_sqft is not None

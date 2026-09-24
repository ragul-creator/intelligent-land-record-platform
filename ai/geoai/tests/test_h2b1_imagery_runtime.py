from io import BytesIO
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_origin

from ai.geoai.runtime.imagery import _edge_connected_bright_background, inspect_and_render_preview
from ai.geoai.parcels.acquisition import create_parcel


def _write_geotiff(
    path: Path,
    *,
    with_nodata_hole: bool = False,
    nodata: int | None = 0,
    white_edge: bool = False,
    white_interior: bool = False,
) -> None:
    profile = {
        "driver": "GTiff",
        "width": 8,
        "height": 6,
        "count": 3,
        "dtype": "uint8",
        "crs": "EPSG:32643",
        "transform": from_origin(500_000, 2_000_000, 2, 2),
        "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        for index in range(1, 4):
            values = np.full((6, 8), 25 * index, dtype=np.uint8)
            if white_edge:
                values[:3, :3] = 255
            if white_interior:
                values[3:5, 4:6] = 255
            if with_nodata_hole:
                values[:2, :3] = 0
            dataset.write(values, index)




def test_preview_fallback_removes_only_edge_connected_white_background() -> None:
    rgb = np.full((6, 8, 3), 90, dtype=np.uint8)
    rgb[:3, :3] = 255
    rgb[3:5, 4:6] = 255
    valid = np.ones((6, 8), dtype=bool)

    removed = _edge_connected_bright_background(rgb, valid)

    assert removed[:3, :3].all()
    assert not removed[3:5, 4:6].any()


def test_unmasked_preview_makes_only_edge_connected_white_transparent(tmp_path: Path) -> None:
    source = tmp_path / "unmasked-white-exterior.tif"
    _write_geotiff(source, nodata=None, white_edge=True, white_interior=True)

    metadata, preview, _ = inspect_and_render_preview(source)

    with Image.open(BytesIO(preview)) as image:
        alpha = np.asarray(image.getchannel("A"))
        assert alpha[:3, :3].max() == 0
        assert alpha[3:5, 4:6].min() == 255
    assert metadata["preview_edge_background_removed"] is True


def test_private_preview_preserves_inspected_georeferencing_without_copying_source(tmp_path: Path) -> None:
    source = tmp_path / "registered.tif"
    _write_geotiff(source)

    metadata, preview, corners = inspect_and_render_preview(source)

    assert metadata["source_crs"] == "EPSG:32643"
    assert metadata["width"] == 8 and metadata["height"] == 6
    assert metadata["transform"]["c"] == 500_000
    assert metadata["preview_crs"] == "EPSG:3857"
    assert metadata["preview_has_alpha"] is True
    assert metadata["preview_edge_background_removed"] is False
    assert metadata["preview_width"] > 0 and metadata["preview_height"] > 0
    assert max(metadata["preview_width"], metadata["preview_height"]) <= 2048
    assert len(corners) == 4
    assert all(-180 <= longitude <= 180 and -90 <= latitude <= 90 for longitude, latitude in corners)
    preview_path = tmp_path / "preview.png"
    preview_path.write_bytes(preview)
    with Image.open(preview_path) as image:
        assert image.mode == "RGBA"
        assert image.size == (metadata["preview_width"], metadata["preview_height"])
        assert image.getchannel("A").getextrema() == (255, 255)
    with rasterio.open(source) as dataset:
        assert dataset.crs.to_epsg() == 32643
        assert dataset.transform.c == 500_000


def test_private_preview_makes_source_nodata_transparent(tmp_path: Path) -> None:
    source = tmp_path / "masked.tif"
    _write_geotiff(source, with_nodata_hole=True)

    _, preview, _ = inspect_and_render_preview(source)

    preview_path = tmp_path / "masked-preview.png"
    preview_path.write_bytes(preview)
    with Image.open(preview_path) as image:
        alpha_min, alpha_max = image.getchannel("A").getextrema()
        assert alpha_min == 0
        assert alpha_max == 255


def test_private_preview_honors_explicit_mask_without_removing_valid_white_edge(tmp_path: Path) -> None:
    source = tmp_path / "explicit-mask.tif"
    _write_geotiff(source, with_nodata_hole=True, white_edge=True, white_interior=True)

    metadata, preview, _ = inspect_and_render_preview(source)

    preview_path = tmp_path / "explicit-mask-preview.png"
    preview_path.write_bytes(preview)
    with Image.open(preview_path) as image:
        alpha = np.asarray(image.getchannel("A"))
        assert alpha[:2, :3].max() == 0
        assert alpha[2, :3].min() == 255
        assert alpha[3:5, 4:6].min() == 255
    assert metadata["preview_edge_background_removed"] is False


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

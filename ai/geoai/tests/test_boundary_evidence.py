from __future__ import annotations

import numpy as np
import rasterio
from pyproj import CRS, Transformer
from rasterio.transform import from_origin
from shapely.geometry import box
from shapely.ops import transform as shapely_transform

from ai.geoai.runtime.boundary_evidence import analyze_candidate_boundaries


def _wgs84(geometry):
    transformer = Transformer.from_crs("EPSG:32644", "EPSG:4326", always_xy=True)
    return shapely_transform(transformer.transform, geometry)


def test_visible_linear_edge_detected_on_high_contrast_boundary(tmp_path) -> None:
    source = tmp_path / "edge.tif"
    image = np.zeros((3, 200, 200), dtype=np.uint8)
    image[:, :, 100:] = 255
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=200,
        height=200,
        count=3,
        dtype="uint8",
        crs="EPSG:32644",
        transform=from_origin(500000, 1200000, 1, 1),
    ) as dataset:
        dataset.write(image)
    parcel = _wgs84(box(500100, 1199850, 500160, 1199950))
    results, metrics = analyze_candidate_boundaries(
        source,
        geometries_wgs84=[parcel],
        roads_wgs84=[],
        metric_crs=CRS.from_epsg(32644),
    )

    assert len(results) == 1
    assert results[0]
    assert any(segment.evidence_type == "VISIBLE_LINEAR_EDGE" for segment in results[0])
    assert metrics["boundary_evidence_model_version"] == "visible-boundary-evidence-v1"
    assert metrics["boundary_edge_count"] == len(results[0])
    assert 0.0 <= metrics["boundary_supported_fraction"] <= 1.0

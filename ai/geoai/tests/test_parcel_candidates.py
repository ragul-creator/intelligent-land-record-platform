from __future__ import annotations

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin
from shapely.geometry import LineString, box
from shapely.ops import transform as shapely_transform

from ai.geoai.runtime.parcel_candidates import SpatialFeature, generate_parcel_candidates


def _wgs84(geometry):
    transformer = Transformer.from_crs("EPSG:32644", "EPSG:4326", always_xy=True)
    return shapely_transform(transformer.transform, geometry)


def test_parcel_candidates_are_non_overlapping_and_bounded(tmp_path) -> None:
    source = tmp_path / "synthetic.tif"
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=600,
        height=400,
        count=3,
        dtype="uint8",
        crs="EPSG:32644",
        transform=from_origin(500000, 1200000, 1, 1),
    ) as dataset:
        dataset.write(np.zeros((3, 400, 600), dtype=np.uint8))

    buildings = [
        SpatialFeature("b1", _wgs84(box(500100, 1199800, 500120, 1199820))),
        SpatialFeature("b2", _wgs84(box(500180, 1199800, 500200, 1199820))),
        SpatialFeature("b3", _wgs84(box(500260, 1199800, 500280, 1199820))),
    ]
    roads = [
        SpatialFeature("r1", _wgs84(LineString([(499995, 1199870), (500605, 1199870)]))),
        SpatialFeature("r2", _wgs84(LineString([(499995, 1199730), (500605, 1199730)]))),
    ]

    result = generate_parcel_candidates(source, buildings=buildings, roads=roads)

    building_candidates = [candidate for candidate in result.candidates if candidate.building_ids]
    assert len(building_candidates) == 3
    assert result.processing_parameters["candidate_count"] == len(result.candidates)
    assert result.processing_parameters["unassigned_building_seed_count"] == 0
    assert result.processing_parameters["land_coverage_ratio"] >= 0.95
    assert all(candidate.area_m2 >= 25 for candidate in result.candidates)
    assert all(candidate.area_m2 <= 2401 for candidate in result.candidates)
    assert all(candidate.confidence <= 0.88 for candidate in result.candidates)
    assert all(candidate.boundary_evidence for candidate in result.candidates)
    assert result.processing_parameters["boundary_edge_count"] > 0
    assert "ROAD_EDGE" in result.processing_parameters["boundary_evidence_type_counts"]
    assert set(result.processing_parameters["boundary_evidence_type_counts"]).issubset({
        "ROAD_EDGE",
        "VISIBLE_LINEAR_EDGE",
        "VEGETATION_EDGE",
        "GEOMETRY_ONLY",
    })

    for index, candidate in enumerate(result.candidates):
        for other in result.candidates[index + 1 :]:
            assert candidate.geometry.intersection(other.geometry).area < 1e-12


def test_oversized_open_cell_is_kept_local_to_seed(tmp_path) -> None:
    source = tmp_path / "open-block.tif"
    with rasterio.open(
        source,
        "w",
        driver="GTiff",
        width=1000,
        height=700,
        count=3,
        dtype="uint8",
        crs="EPSG:32644",
        transform=from_origin(500000, 1200000, 1, 1),
    ) as dataset:
        dataset.write(np.zeros((3, 700, 1000), dtype=np.uint8))

    buildings = [
        SpatialFeature("b1", _wgs84(box(500120, 1199700, 500140, 1199720))),
        SpatialFeature("b2", _wgs84(box(500220, 1199700, 500240, 1199720))),
    ]
    roads = [
        SpatialFeature("r1", _wgs84(LineString([(499995, 1199800), (501005, 1199800)]))),
    ]

    result = generate_parcel_candidates(source, buildings=buildings, roads=roads)

    building_candidates = [candidate for candidate in result.candidates if candidate.building_ids]
    assert len(building_candidates) == 2
    assert result.processing_parameters["unassigned_building_seed_count"] == 0
    assert result.processing_parameters["land_coverage_ratio"] >= 0.95
    assert max(candidate.area_m2 for candidate in result.candidates) <= 2401
    assert all(
        "legal boundary requires cadastral/FMB or survey verification"
        in candidate.warnings[0]
        for candidate in result.candidates
    )

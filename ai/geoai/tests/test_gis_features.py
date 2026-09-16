"""Synthetic C.5 tests for source-backed roads/pathways and land-use features."""

from __future__ import annotations

import copy

import pytest
from shapely.geometry import LineString, Polygon, shape

from ai.geoai.features.export import feature_collection
from ai.geoai.features.land_use import create_land_use
from ai.geoai.features.roads import create_road
from ai.geoai.features.validation import FeatureValidationError
from ai.geoai.polygonization.geometry import SQUARE_FEET_PER_SQUARE_METRE


def _projected_line() -> dict[str, object]:
    return {"type": "LineString", "coordinates": [[500000, 3000000], [500010, 3000000]]}


def _projected_polygon() -> dict[str, object]:
    return {"type": "Polygon", "coordinates": [[[500000, 3000000], [500010, 3000000], [500010, 3000010], [500000, 3000010], [500000, 3000000]]]}


def test_projected_road_has_metre_length_and_preserves_provenance() -> None:
    road = create_road("EXISTING_GIS", _projected_line(), "ROAD", source_crs="EPSG:32618", source_reference="roads-v1")
    assert road.length_m == pytest.approx(10.0)
    assert road.source_reference == "roads-v1"
    assert road.coordinate_space == "WORLD"


def test_geographic_road_uses_geodesic_length() -> None:
    road = create_road("EXISTING_GIS", {"type": "LineString", "coordinates": [[77.0, 28.0], [77.001, 28.0]]}, "ROAD", source_crs="EPSG:4326")
    assert road.length_m is not None
    assert road.length_m > 90
    assert road.length_m != pytest.approx(0.001)


def test_local_road_has_no_real_world_length() -> None:
    road = create_road("MANUAL_DRAWN", {"coordinates": [[0, 0], [20, 0]], "coordinate_space": "PIXEL"}, "PATHWAY")
    assert road.coordinate_space == "PIXEL"
    assert road.length_m is None


def test_one_point_road_is_rejected() -> None:
    with pytest.raises(FeatureValidationError, match="two distinct"):
        create_road("MANUAL_DRAWN", {"coordinates": [[0, 0]]}, "ROAD")


def test_ai_road_stays_draft_and_unverified() -> None:
    road = create_road("AI_CANDIDATE", _projected_line(), "ACCESS_CORRIDOR", source_crs="EPSG:32618", model_version="road-candidate-v1")
    assert (road.status, road.verification_status, road.ai_status) == ("DRAFT", "UNVERIFIED", "AI_PRELIMINARY")
    assert road.model_version == "road-candidate-v1"


def test_geojson_feature_properties_supply_road_surface_and_source_crs() -> None:
    payload = {
        "type": "Feature",
        "properties": {"road_surface": True, "source_crs": "EPSG:32618"},
        "geometry": _projected_polygon(),
    }
    road = create_road("EXISTING_GIS", payload, "ROAD")
    assert (road.geometry_kind, road.coordinate_space, road.length_m) == ("SURFACE", "WORLD", pytest.approx(40.0))


def test_projected_land_use_area_and_square_feet() -> None:
    feature = create_land_use("EXISTING_GIS", _projected_polygon(), "RESIDENTIAL", source_crs="EPSG:32618")
    assert feature.area_m2 == pytest.approx(100.0)
    assert feature.area_sqft == pytest.approx(100.0 * SQUARE_FEET_PER_SQUARE_METRE)


def test_geographic_land_use_never_uses_degree_squared() -> None:
    feature = create_land_use("EXISTING_GIS", {"type": "Polygon", "coordinates": [[[77.0, 28.0], [77.001, 28.0], [77.001, 28.001], [77.0, 28.001], [77.0, 28.0]]]}, "GREEN_OPEN_SPACE", source_crs="EPSG:4326")
    assert feature.area_m2 is not None
    assert feature.area_m2 > 1_000
    assert feature.area_m2 != pytest.approx(0.000001)


def test_local_land_use_has_null_real_world_area_and_unknown_is_allowed() -> None:
    feature = create_land_use("MANUAL_DRAWN", {"coordinates": [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]], "coordinate_space": "LOCAL"}, "UNKNOWN")
    assert feature.land_use_class.value == "UNKNOWN"
    assert feature.area_m2 is None
    assert feature.area_sqft is None


def test_ai_land_use_stays_preliminary_and_input_is_not_mutated() -> None:
    payload = _projected_polygon()
    original = copy.deepcopy(payload)
    feature = create_land_use("AI_CANDIDATE", payload, "VACANT", source_crs="EPSG:32618")
    assert payload == original
    assert (feature.status, feature.verification_status, feature.ai_status) == ("DRAFT", "UNVERIFIED", "AI_PRELIMINARY")


def test_geojson_export_round_trip_preserves_feature_properties() -> None:
    road = create_road("EXISTING_GIS", _projected_line(), "ROAD", source_crs="EPSG:32618")
    exported = feature_collection(road)
    item = exported["features"][0]
    assert exported["metadata"]["export_crs"] == "EPSG:4326"
    assert item["properties"]["source_crs"] == "EPSG:32618"
    assert shape(item["geometry"]).geom_type == "LineString"
    assert not LineString(item["geometry"]["coordinates"]).is_empty


def test_polygon_input_remains_geometry_equivalent() -> None:
    payload = _projected_polygon()
    feature = create_land_use("EXISTING_GIS", payload, "COMMERCIAL", source_crs="EPSG:32618")
    assert feature.geometry.equals(Polygon(payload["coordinates"][0]))

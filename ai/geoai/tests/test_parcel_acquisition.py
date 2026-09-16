import copy
import json
from pathlib import Path

import pytest

from ai.geoai.parcels.acquisition import ParcelValidationError, create_parcel, load_json_input
from ai.geoai.parcels.export import parcel_feature_collection, write_parcel_geojson


PROJECTED_POLYGON = {
    "type": "Polygon",
    "coordinates": [[[500000, 3000000], [500010, 3000000], [500010, 3000010], [500000, 3000010], [500000, 3000000]]],
}


def test_cadastral_geojson_preserves_provenance_and_metric_area() -> None:
    payload = {"type": "Feature", "geometry": PROJECTED_POLYGON, "source_reference": "cadastral-v1", "parcel_id": "parcel-cad-1"}

    parcel = create_parcel("CADASTRAL_GIS", payload, source_crs="EPSG:32618")

    assert parcel.parcel_id == "parcel-cad-1"
    assert parcel.source.value == "CADASTRAL_GIS"
    assert parcel.source_reference == "cadastral-v1"
    assert parcel.coordinate_space == "WORLD"
    assert parcel.area_m2 == pytest.approx(100.0)
    assert parcel.area_sqft == pytest.approx(1076.39104167)


def test_human_drawn_coordinates_accept_direct_polygon_input() -> None:
    payload = {"coordinates": PROJECTED_POLYGON["coordinates"], "coordinate_space": "PIXEL", "notes": "manual draft"}

    parcel = create_parcel("HUMAN_DRAWN", payload)

    assert parcel.source.value == "HUMAN_DRAWN"
    assert parcel.coordinate_space == "PIXEL"
    assert parcel.area_m2 is None
    assert parcel.notes == ("manual draft",)


def test_gnss_points_construct_closed_polygon_and_preserve_original_order() -> None:
    points = [[500000, 3000000], [500010, 3000000], [500010, 3000010], [500000, 3000010]]

    parcel = create_parcel("GNSS_SURVEY", {"points": points}, source_crs="EPSG:32618")

    assert parcel.survey_points == tuple((float(x), float(y)) for x, y in points)
    assert tuple(parcel.geometry.exterior.coords[0]) == tuple(parcel.geometry.exterior.coords[-1])
    assert parcel.area_m2 == pytest.approx(100.0)


def test_self_intersection_that_repairs_to_multipolygon_is_rejected_by_default() -> None:
    bow_tie = {"type": "Polygon", "coordinates": [[[0, 0], [2, 2], [2, 0], [0, 2], [0, 0]]]}

    with pytest.raises(ParcelValidationError, match="MultiPolygon"):
        create_parcel("HUMAN_DRAWN", bow_tie, source_crs="EPSG:32618")


def test_geographic_crs_uses_geodesic_area_not_degree_squared() -> None:
    polygon = {"type": "Polygon", "coordinates": [[[77, 28], [77.001, 28], [77.001, 28.001], [77, 28.001], [77, 28]]]}

    parcel = create_parcel("CADASTRAL_GIS", polygon, source_crs="EPSG:4326")

    assert parcel.geometry.area == pytest.approx(0.000001)
    assert parcel.area_m2 is not None and parcel.area_m2 > 1000.0


def test_local_coordinates_keep_real_world_area_null() -> None:
    parcel = create_parcel("HUMAN_DRAWN", {"coordinates": PROJECTED_POLYGON["coordinates"], "coordinate_space": "LOCAL"})

    assert parcel.coordinate_space == "LOCAL"
    assert parcel.area_m2 is None
    assert parcel.area_sqft is None


def test_ai_visible_boundary_is_preliminary_and_unverified() -> None:
    payload = {"geometry": PROJECTED_POLYGON, "evidence_type": "FENCE", "confidence": 0.72, "model_version": "edge-model-v1"}

    parcel = create_parcel("AI_VISIBLE_BOUNDARY", payload, source_crs="EPSG:32618")

    assert parcel.status == "DRAFT"
    assert parcel.verification_status == "UNVERIFIED"
    assert parcel.ai_boundary_status == "AI_PRELIMINARY"
    assert parcel.requires_survey is True
    assert parcel.evidence_type.value == "FENCE"


def test_no_visible_evidence_never_generates_a_guessed_polygon() -> None:
    parcel = create_parcel("AI_VISIBLE_BOUNDARY", {"evidence_type": "NO_VISIBLE_EVIDENCE", "coordinate_space": "PIXEL"})

    assert parcel.geometry is None
    assert parcel.status == "NOT_DETERMINED"
    assert parcel.requires_survey is True
    assert "cannot determine" in parcel.warnings[0]
    assert parcel_feature_collection(parcel)["features"][0]["geometry"] is None


def test_fmb_import_and_source_geometry_are_not_mutated() -> None:
    payload = {"type": "Feature", "geometry": PROJECTED_POLYGON, "source_reference": "fmb-georeferenced-v1"}
    original = copy.deepcopy(payload)

    parcel = create_parcel("FMB_IMPORT", payload, source_crs="EPSG:32618")

    assert payload == original
    assert parcel.source.value == "FMB_IMPORT"
    assert parcel.source_reference == "fmb-georeferenced-v1"


def test_export_and_json_input_round_trip(tmp_path: Path) -> None:
    input_path = tmp_path / "parcel.json"
    output_path = tmp_path / "parcel.geojson"
    input_path.write_text(json.dumps(PROJECTED_POLYGON), encoding="utf-8")

    parcel = create_parcel("CADASTRAL_GIS", load_json_input(input_path), source_crs="EPSG:32618")
    write_parcel_geojson(parcel, output_path)
    collection = json.loads(output_path.read_text(encoding="utf-8"))

    assert collection["type"] == "FeatureCollection"
    assert collection["features"][0]["properties"]["source_crs"] == "EPSG:32618"
    assert collection["features"][0]["properties"]["crs"] == "EPSG:4326"

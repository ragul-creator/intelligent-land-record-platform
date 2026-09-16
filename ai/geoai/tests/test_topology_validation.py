from __future__ import annotations

import pytest
from shapely.geometry import Polygon

from ai.geoai.topology.validation import (
    TopologyValidationError,
    compare_parcels,
    normalize_parcel_geometry,
    validate_edited_parcel,
    validate_parcel_topology,
)


def square(x0: float, y0: float, size: float = 10.0) -> Polygon:
    return Polygon([(x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size), (x0, y0)])


def test_normalize_repairs_self_intersection() -> None:
    bowtie = Polygon([(0, 0), (10, 10), (0, 10), (10, 0), (0, 0)])
    repaired = normalize_parcel_geometry(bowtie)
    assert repaired.is_valid
    assert repaired.area > 0


def test_normalize_rejects_empty_geometry() -> None:
    with pytest.raises(TopologyValidationError):
        normalize_parcel_geometry(Polygon())


def test_topology_detects_projected_overlap() -> None:
    report = validate_parcel_topology(
        {"p1": square(0, 0), "p2": square(9, 0)},
        source_crs="EPSG:32643",
    )
    assert report.valid
    assert report.review_required
    issue = next(issue for issue in report.issues if issue.code == "PARCEL_OVERLAP")
    assert issue.area_m2 == pytest.approx(10.0)


def test_touching_boundaries_are_not_overlap() -> None:
    report = validate_parcel_topology(
        {"p1": square(0, 0), "p2": square(10, 0)},
        source_crs="EPSG:32643",
    )
    assert not any(issue.code == "PARCEL_OVERLAP" for issue in report.issues)


def test_coverage_gap_is_detected() -> None:
    boundary = Polygon([(0, 0), (30, 0), (30, 10), (0, 10), (0, 0)])
    report = validate_parcel_topology(
        {"p1": square(0, 0), "p2": square(20, 0)},
        source_crs="EPSG:32643",
        coverage_boundary=boundary,
    )
    issue = next(issue for issue in report.issues if issue.code == "COVERAGE_GAP")
    assert issue.area_m2 == pytest.approx(100.0)


def test_outside_project_boundary_is_detected() -> None:
    boundary = square(0, 0, 20)
    report = validate_parcel_topology(
        {"p1": square(15, 0, 10)},
        source_crs="EPSG:32643",
        coverage_boundary=boundary,
    )
    assert any(issue.code == "OUTSIDE_PROJECT_BOUNDARY" for issue in report.issues)


def test_compare_parcels_reports_area_change_and_difference() -> None:
    result = compare_parcels(square(0, 0, 10), square(0, 0, 11), source_crs="EPSG:32643")
    assert result.reference_area_m2 == pytest.approx(100.0)
    assert result.candidate_area_m2 == pytest.approx(121.0)
    assert result.area_change_percent == pytest.approx(21.0)
    assert result.symmetric_difference_area_m2 == pytest.approx(21.0)
    assert result.hausdorff_unit == "m"


def test_compare_geographic_geometry_never_uses_degree_squared_as_m2() -> None:
    reference = Polygon([(77.0, 11.0), (77.001, 11.0), (77.001, 11.001), (77.0, 11.001), (77.0, 11.0)])
    candidate = Polygon([(77.0, 11.0), (77.0011, 11.0), (77.0011, 11.001), (77.0, 11.001), (77.0, 11.0)])
    result = compare_parcels(reference, candidate, source_crs="EPSG:4326")
    assert result.reference_area_m2 is not None and result.reference_area_m2 > 1000
    assert result.symmetric_difference_area_m2 is not None and result.symmetric_difference_area_m2 > 0
    assert result.hausdorff_distance is None


def test_edited_parcel_significant_area_change_requires_review() -> None:
    result = validate_edited_parcel(
        square(0, 0, 10),
        square(0, 0, 11),
        parcel_id="p1",
        source_crs="EPSG:32643",
        area_change_review_percent=5,
    )
    assert result.status == "REVIEW_REQUIRED"
    assert any(issue.code == "SIGNIFICANT_AREA_CHANGE" for issue in result.issues)


def test_small_valid_edit_can_pass_without_review() -> None:
    original = square(0, 0, 10)
    edited = Polygon([(0, 0), (10.1, 0), (10.1, 10), (0, 10), (0, 0)])
    result = validate_edited_parcel(
        original,
        edited,
        parcel_id="p1",
        source_crs="EPSG:32643",
        area_change_review_percent=5,
    )
    assert result.status == "VALID"
    assert result.geometry is not None


def test_edited_parcel_neighbour_overlap_requires_review() -> None:
    result = validate_edited_parcel(
        square(0, 0),
        square(0, 0, 10.5),
        parcel_id="p1",
        source_crs="EPSG:32643",
        neighbouring_parcels={"p2": square(10, 0)},
        area_change_review_percent=20,
    )
    assert result.status == "REVIEW_REQUIRED"
    assert any(issue.code == "NEIGHBOUR_OVERLAP" for issue in result.issues)


def test_local_coordinates_do_not_invent_real_world_area() -> None:
    result = validate_edited_parcel(
        square(0, 0),
        square(0, 0, 11),
        parcel_id="p1",
        source_crs=None,
    )
    assert result.area_before_m2 is None
    assert result.area_after_m2 is None
    assert result.area_change_percent is None

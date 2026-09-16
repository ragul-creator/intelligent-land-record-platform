"""CRS-safe parcel topology validation for Phase C.6.

The functions here are deliberately conservative: they report topology and edit
risks without silently converting preliminary geometry into an approved legal
boundary. They operate on Shapely geometries and declared CRS values so Phase
D/C.7 can reuse them for Web-GIS edits and PostGIS persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from typing import Iterable, Mapping

from pyproj import CRS
from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from ai.geoai.polygonization.geometry import area_square_metres


class TopologyValidationError(ValueError):
    """Raised when topology validation cannot be performed safely."""


@dataclass(frozen=True)
class TopologyIssue:
    code: str
    severity: str
    parcel_ids: tuple[str, ...]
    message: str
    area_m2: float | None = None


@dataclass(frozen=True)
class TopologyReport:
    valid: bool
    review_required: bool
    issues: tuple[TopologyIssue, ...]


@dataclass(frozen=True)
class ComparisonResult:
    reference_area_m2: float | None
    candidate_area_m2: float | None
    symmetric_difference_area_m2: float | None
    overlap_area_m2: float | None
    area_change_percent: float | None
    hausdorff_distance: float | None
    hausdorff_unit: str | None


@dataclass(frozen=True)
class EditValidationResult:
    status: str
    geometry: Polygon | MultiPolygon | None
    area_before_m2: float | None
    area_after_m2: float | None
    area_change_percent: float | None
    issues: tuple[TopologyIssue, ...]


def _polygonal_parts(geometry: BaseGeometry) -> list[Polygon | MultiPolygon]:
    if isinstance(geometry, (Polygon, MultiPolygon)):
        return [geometry]
    if isinstance(geometry, GeometryCollection):
        return [part for part in geometry.geoms if isinstance(part, (Polygon, MultiPolygon)) and not part.is_empty]
    return []


def normalize_parcel_geometry(geometry: BaseGeometry) -> Polygon | MultiPolygon:
    """Conservatively repair polygonal geometry without rectangularizing it."""
    if geometry is None or geometry.is_empty:
        raise TopologyValidationError("Parcel geometry must not be empty.")
    candidate = geometry if geometry.is_valid else make_valid(geometry)
    parts = _polygonal_parts(candidate)
    if not parts:
        raise TopologyValidationError("Parcel geometry must be Polygon or MultiPolygon after repair.")
    merged = unary_union(parts)
    if not isinstance(merged, (Polygon, MultiPolygon)) or merged.is_empty:
        raise TopologyValidationError("Parcel geometry could not be normalized to polygonal geometry.")
    if not merged.is_valid:
        merged = make_valid(merged)
    if not isinstance(merged, (Polygon, MultiPolygon)) or merged.is_empty or not merged.is_valid:
        raise TopologyValidationError("Parcel geometry remains invalid after conservative repair.")
    if merged.area <= 0:
        raise TopologyValidationError("Parcel geometry must have non-zero area.")
    return merged


def _same_crs(crs: str | CRS | None) -> CRS | None:
    if crs is None:
        return None
    return CRS.from_user_input(crs)


def _area_change_percent(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before <= 0:
        return None
    return ((after - before) / before) * 100.0


def compare_parcels(
    reference: BaseGeometry,
    candidate: BaseGeometry,
    *,
    source_crs: str | CRS | None,
) -> ComparisonResult:
    """Compare a draft parcel with a cadastral/GIS reference in the same CRS."""
    ref = normalize_parcel_geometry(reference)
    cand = normalize_parcel_geometry(candidate)
    parsed = _same_crs(source_crs)
    ref_area = area_square_metres(ref, parsed)
    cand_area = area_square_metres(cand, parsed)
    symmetric_difference = ref.symmetric_difference(cand)
    overlap = ref.intersection(cand)
    symmetric_m2 = area_square_metres(symmetric_difference, parsed) if parsed is not None else None
    overlap_m2 = area_square_metres(overlap, parsed) if parsed is not None else None
    change = _area_change_percent(ref_area, cand_area)

    hausdorff_distance: float | None = None
    hausdorff_unit: str | None = None
    if parsed is not None and parsed.is_projected and len(parsed.axis_info) >= 1:
        factor = parsed.axis_info[0].unit_conversion_factor
        if factor and factor > 0:
            hausdorff_distance = float(ref.hausdorff_distance(cand) * factor)
            hausdorff_unit = "m"

    return ComparisonResult(
        reference_area_m2=ref_area,
        candidate_area_m2=cand_area,
        symmetric_difference_area_m2=symmetric_m2,
        overlap_area_m2=overlap_m2,
        area_change_percent=change,
        hausdorff_distance=hausdorff_distance,
        hausdorff_unit=hausdorff_unit,
    )


def validate_parcel_topology(
    parcels: Mapping[str, BaseGeometry],
    *,
    source_crs: str | CRS | None,
    coverage_boundary: BaseGeometry | None = None,
    overlap_tolerance_m2: float = 0.01,
    gap_tolerance_m2: float = 0.01,
) -> TopologyReport:
    """Report invalid parcels, pairwise overlaps, and optional coverage gaps."""
    if overlap_tolerance_m2 < 0 or gap_tolerance_m2 < 0:
        raise TopologyValidationError("Topology tolerances must be zero or greater.")
    parsed = _same_crs(source_crs)
    normalized: dict[str, Polygon | MultiPolygon] = {}
    issues: list[TopologyIssue] = []

    for parcel_id, geometry in parcels.items():
        try:
            normalized[parcel_id] = normalize_parcel_geometry(geometry)
        except TopologyValidationError as error:
            issues.append(TopologyIssue("INVALID_GEOMETRY", "ERROR", (parcel_id,), str(error)))

    for (left_id, left), (right_id, right) in combinations(normalized.items(), 2):
        if not left.intersects(right):
            continue
        intersection = left.intersection(right)
        if intersection.is_empty or intersection.area <= 0:
            continue
        overlap_m2 = area_square_metres(intersection, parsed) if parsed is not None else None
        if overlap_m2 is None or overlap_m2 > overlap_tolerance_m2:
            issues.append(
                TopologyIssue(
                    "PARCEL_OVERLAP",
                    "REVIEW",
                    (left_id, right_id),
                    "Parcel interiors overlap and require review.",
                    overlap_m2,
                )
            )

    if coverage_boundary is not None:
        boundary = normalize_parcel_geometry(coverage_boundary)
        union = unary_union(list(normalized.values())) if normalized else GeometryCollection()
        outside = union.difference(boundary)
        outside_m2 = area_square_metres(outside, parsed) if parsed is not None and not outside.is_empty else 0.0
        if not outside.is_empty and outside.area > 0 and (outside_m2 is None or outside_m2 > overlap_tolerance_m2):
            issues.append(
                TopologyIssue(
                    "OUTSIDE_PROJECT_BOUNDARY",
                    "REVIEW",
                    tuple(normalized.keys()),
                    "One or more parcel areas extend outside the declared project boundary.",
                    outside_m2,
                )
            )
        gaps = boundary.difference(union)
        gaps_m2 = area_square_metres(gaps, parsed) if parsed is not None and not gaps.is_empty else 0.0
        if not gaps.is_empty and gaps.area > 0 and (gaps_m2 is None or gaps_m2 > gap_tolerance_m2):
            issues.append(
                TopologyIssue(
                    "COVERAGE_GAP",
                    "REVIEW",
                    tuple(normalized.keys()),
                    "Coverage contains a gap larger than the configured tolerance.",
                    gaps_m2,
                )
            )

    has_error = any(issue.severity == "ERROR" for issue in issues)
    has_review = any(issue.severity == "REVIEW" for issue in issues)
    return TopologyReport(valid=not has_error, review_required=has_review or has_error, issues=tuple(issues))


def validate_edited_parcel(
    original: BaseGeometry,
    edited: BaseGeometry,
    *,
    parcel_id: str,
    source_crs: str | CRS | None,
    neighbouring_parcels: Mapping[str, BaseGeometry] | None = None,
    project_boundary: BaseGeometry | None = None,
    area_change_review_percent: float = 5.0,
    overlap_tolerance_m2: float = 0.01,
) -> EditValidationResult:
    """Validate a human-edited draft while preserving the original externally.

    Significant changes are flagged REVIEW_REQUIRED rather than rejected. Invalid
    geometry is rejected because it cannot safely enter later PostGIS/Web-GIS
    workflows.
    """
    if area_change_review_percent < 0:
        raise TopologyValidationError("area_change_review_percent must be zero or greater.")
    parsed = _same_crs(source_crs)
    original_norm = normalize_parcel_geometry(original)
    issues: list[TopologyIssue] = []
    try:
        edited_norm = normalize_parcel_geometry(edited)
    except TopologyValidationError as error:
        return EditValidationResult(
            status="INVALID",
            geometry=None,
            area_before_m2=area_square_metres(original_norm, parsed),
            area_after_m2=None,
            area_change_percent=None,
            issues=(TopologyIssue("INVALID_EDIT_GEOMETRY", "ERROR", (parcel_id,), str(error)),),
        )

    before_m2 = area_square_metres(original_norm, parsed)
    after_m2 = area_square_metres(edited_norm, parsed)
    change = _area_change_percent(before_m2, after_m2)
    if change is not None and abs(change) > area_change_review_percent:
        issues.append(
            TopologyIssue(
                "SIGNIFICANT_AREA_CHANGE",
                "REVIEW",
                (parcel_id,),
                f"Edited parcel area changed by {change:.2f}%; human review is required.",
                abs((after_m2 or 0.0) - (before_m2 or 0.0)),
            )
        )

    if project_boundary is not None:
        boundary = normalize_parcel_geometry(project_boundary)
        outside = edited_norm.difference(boundary)
        outside_m2 = area_square_metres(outside, parsed) if parsed is not None and not outside.is_empty else 0.0
        if not outside.is_empty and outside.area > 0 and (outside_m2 is None or outside_m2 > overlap_tolerance_m2):
            issues.append(
                TopologyIssue(
                    "OUTSIDE_PROJECT_BOUNDARY",
                    "REVIEW",
                    (parcel_id,),
                    "Edited parcel extends outside the project boundary.",
                    outside_m2,
                )
            )

    for neighbour_id, neighbour_geometry in (neighbouring_parcels or {}).items():
        neighbour = normalize_parcel_geometry(neighbour_geometry)
        overlap = edited_norm.intersection(neighbour)
        if overlap.is_empty or overlap.area <= 0:
            continue
        overlap_m2 = area_square_metres(overlap, parsed) if parsed is not None else None
        if overlap_m2 is None or overlap_m2 > overlap_tolerance_m2:
            issues.append(
                TopologyIssue(
                    "NEIGHBOUR_OVERLAP",
                    "REVIEW",
                    (parcel_id, neighbour_id),
                    "Edited parcel overlaps a neighbouring parcel.",
                    overlap_m2,
                )
            )

    status = "REVIEW_REQUIRED" if issues else "VALID"
    return EditValidationResult(
        status=status,
        geometry=edited_norm,
        area_before_m2=before_m2,
        area_after_m2=after_m2,
        area_change_percent=change,
        issues=tuple(issues),
    )

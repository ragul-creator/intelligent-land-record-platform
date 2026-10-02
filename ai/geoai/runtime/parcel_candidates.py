"""Generate preliminary plot candidates from registered imagery, buildings, and roads.

The output is deliberately non-cadastral. It partitions road-bounded land around
building seeds and must be survey/FMB verified before any legal use.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from pathlib import Path
from typing import Iterable

from pyproj import CRS, Transformer
import rasterio
from rasterio.warp import transform_bounds
from shapely import affinity
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Point, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import split as shapely_split, transform as shapely_transform, unary_union, voronoi_diagram

from ai.geoai.runtime.boundary_evidence import (
    BOUNDARY_EVIDENCE_MODEL_VERSION,
    BoundaryEvidenceSegment,
    analyze_candidate_boundaries,
)


MODEL_VERSION = "road-block-full-coverage-parcel-candidate-v3"


@dataclass(frozen=True)
class SpatialFeature:
    id: str
    geometry: BaseGeometry


@dataclass(frozen=True)
class ParcelCandidate:
    geometry: BaseGeometry
    confidence: float
    building_ids: tuple[str, ...]
    road_frontage_m: float
    area_m2: float
    area_sqft: float
    block_index: int
    warnings: tuple[str, ...]
    boundary_evidence: tuple[BoundaryEvidenceSegment, ...] = ()


@dataclass(frozen=True)
class ParcelCandidateResult:
    candidates: tuple[ParcelCandidate, ...]
    processing_parameters: dict[str, object]


def _utm_crs(longitude: float, latitude: float) -> CRS:
    zone = max(1, min(60, int(math.floor((longitude + 180.0) / 6.0)) + 1))
    return CRS.from_epsg((32600 if latitude >= 0 else 32700) + zone)


def _polygon_parts(geometry: BaseGeometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    if isinstance(geometry, MultiPolygon):
        return [part for part in geometry.geoms if not part.is_empty]
    return [part for part in getattr(geometry, "geoms", ()) if isinstance(part, Polygon) and not part.is_empty]


def _clean_polygon(geometry: BaseGeometry) -> BaseGeometry:
    if geometry.is_empty:
        return geometry
    cleaned = geometry.buffer(0)
    return cleaned if not cleaned.is_empty else geometry


def _raster_footprint_wgs84(source_path: str | Path) -> Polygon:
    with rasterio.open(source_path) as dataset:
        if dataset.crs is None:
            raise ValueError("Parcel candidate generation requires georeferenced imagery.")
        left, bottom, right, top = transform_bounds(
            dataset.crs,
            "EPSG:4326",
            *dataset.bounds,
            densify_pts=21,
        )
    return box(left, bottom, right, top)


def _metric_transformers(footprint: BaseGeometry) -> tuple[CRS, Transformer, Transformer]:
    centroid = footprint.centroid
    metric = _utm_crs(float(centroid.x), float(centroid.y))
    return (
        metric,
        Transformer.from_crs("EPSG:4326", metric, always_xy=True),
        Transformer.from_crs(metric, "EPSG:4326", always_xy=True),
    )
def _candidate_score(
    *,
    area_m2: float,
    road_frontage_m: float,
    building_count: int,
    crosses_building: bool,
    block_seed_count: int,
) -> float:
    score = 0.48
    if road_frontage_m >= 3.0:
        score += 0.14
    elif road_frontage_m > 0:
        score += 0.06
    if building_count == 1:
        score += 0.12
    elif building_count > 1:
        score += 0.04
    if 80.0 <= area_m2 <= 2200.0:
        score += 0.08
    if block_seed_count >= 2:
        score += 0.05
    if crosses_building:
        score -= 0.16
    if area_m2 > 3500.0:
        score -= 0.10
    return max(0.30, min(0.88, score))


def _frontage_length(candidate: BaseGeometry, road_corridor: BaseGeometry) -> float:
    if road_corridor.is_empty:
        return 0.0
    # Candidate edges created by subtracting the road corridor lie on, or very
    # close to, the corridor boundary. A 0.6 m tolerance keeps this robust
    # after simplification without treating interior edges as frontage.
    frontage_zone = road_corridor.boundary.buffer(0.6)
    return float(candidate.boundary.intersection(frontage_zone).length)


def _centroid_voronoi_cells(
    block: Polygon,
    seeds: list[tuple[str, BaseGeometry]],
) -> dict[str, BaseGeometry]:
    """Return non-overlapping ownership cells around building centroids."""
    if len(seeds) == 1:
        return {seeds[0][0]: block}

    points = MultiPoint([geometry.centroid for _, geometry in seeds])
    diagram = voronoi_diagram(points, envelope=block.envelope.buffer(5.0), edges=False)
    by_seed: dict[str, list[BaseGeometry]] = {seed_id: [] for seed_id, _ in seeds}
    for cell in getattr(diagram, "geoms", (diagram,)):
        clipped = _clean_polygon(cell.intersection(block))
        if clipped.is_empty:
            continue
        representative = clipped.representative_point()
        seed_id, _ = min(
            seeds,
            key=lambda item: representative.distance(item[1].centroid),
        )
        by_seed[seed_id].append(clipped)

    return {
        seed_id: _clean_polygon(unary_union(by_seed.get(seed_id) or []))
        for seed_id, _ in seeds
    }


def _linear_parts(geometry: BaseGeometry) -> list[BaseGeometry]:
    if geometry.geom_type in {"LineString", "LinearRing"}:
        return [geometry]
    if geometry.geom_type == "Polygon":
        return [geometry.exterior]
    if geometry.geom_type == "MultiPolygon":
        return [polygon.exterior for polygon in geometry.geoms]
    return [
        part
        for part in getattr(geometry, "geoms", ())
        if part.geom_type in {"LineString", "LinearRing"}
    ]


def _nearest_road_line(
    seed_geometry: BaseGeometry,
    roads: list[SpatialFeature],
) -> tuple[str, BaseGeometry, float, float] | None:
    centroid = seed_geometry.centroid
    choices: list[tuple[float, str, BaseGeometry, float]] = []
    for road in roads:
        for index, line in enumerate(_linear_parts(road.geometry)):
            if line.length <= 0:
                continue
            measure = float(line.project(centroid))
            distance = float(centroid.distance(line))
            choices.append((distance, f"{road.id}:{index}", line, measure))
    if not choices:
        return None
    distance, key, line, measure = min(choices, key=lambda item: item[0])
    return key, line, measure, distance


def _unit_tangent(line: BaseGeometry, measure: float) -> tuple[float, float]:
    epsilon = min(2.0, max(0.25, float(line.length) * 0.02))
    start = line.interpolate(max(0.0, measure - epsilon))
    end = line.interpolate(min(float(line.length), measure + epsilon))
    dx, dy = float(end.x - start.x), float(end.y - start.y)
    norm = math.hypot(dx, dy)
    return (1.0, 0.0) if norm < 1e-9 else (dx / norm, dy / norm)


def _projection_span(
    geometry: BaseGeometry,
    origin: Point,
    axis: tuple[float, float],
) -> tuple[float, float]:
    coordinates = list(geometry.convex_hull.exterior.coords)
    values = [
        (float(x) - origin.x) * axis[0] + (float(y) - origin.y) * axis[1]
        for x, y in coordinates
    ]
    return min(values), max(values)


def _frontage_strip_cells(
    block: Polygon,
    seeds: list[tuple[str, BaseGeometry]],
    roads: list[SpatialFeature],
    *,
    max_road_distance_m: float = 55.0,
) -> list[tuple[str, BaseGeometry]]:
    """Create frontage-oriented plot candidates with a Voronoi safety mask.

    Buildings assigned to the same road side are ordered along that road.
    Their side boundaries are placed halfway between frontage anchors, while
    depth runs inward from the road. A centroid-Voronoi ownership mask is used
    only as a collision guard at bends and junctions, keeping candidates
    non-overlapping and preventing separators from crossing neighbouring roofs.
    """
    ownership = _centroid_voronoi_cells(block, seeds)

    # Reserve every detected building for its own ownership cell before
    # applying frontage strips. This prevents a centroid bisector from cutting
    # through a roof while keeping ownership cells mutually exclusive.
    protections = {
        seed_id: _clean_polygon(seed_geometry.intersection(block))
        for seed_id, seed_geometry in seeds
    }
    protected_union = unary_union([
        geometry for geometry in protections.values() if not geometry.is_empty
    ])
    for seed_id, owner in list(ownership.items()):
        protection = protections.get(seed_id, Polygon())
        ownership[seed_id] = _clean_polygon(
            owner.difference(protected_union).union(protection)
        )

    assignments: dict[str, tuple[str, BaseGeometry, float, float, int]] = {}
    groups: dict[tuple[str, int], list[tuple[str, float]]] = {}

    for seed_id, seed_geometry in seeds:
        nearest = _nearest_road_line(seed_geometry, roads)
        if nearest is None:
            continue
        road_key, line, measure, distance = nearest
        anchor = line.interpolate(measure)
        tangent = _unit_tangent(line, measure)
        normal_left = (-tangent[1], tangent[0])
        to_building = (
            float(seed_geometry.centroid.x - anchor.x),
            float(seed_geometry.centroid.y - anchor.y),
        )
        side = 1 if (to_building[0] * normal_left[0] + to_building[1] * normal_left[1]) >= 0 else -1
        assignments[seed_id] = (road_key, line, measure, distance, side)
        if distance <= max_road_distance_m:
            groups.setdefault((road_key, side), []).append((seed_id, measure))

    cells: list[tuple[str, BaseGeometry]] = []
    for seed_id, seed_geometry in seeds:
        owner = ownership.get(seed_id)
        assignment = assignments.get(seed_id)
        if owner is None or owner.is_empty:
            continue
        if assignment is None or assignment[3] > max_road_distance_m:
            cells.append((seed_id, owner))
            continue

        road_key, line, measure, _distance, side = assignment
        group = sorted(groups.get((road_key, side), []), key=lambda item: item[1])
        position = next((index for index, item in enumerate(group) if item[0] == seed_id), 0)
        previous_measure = group[position - 1][1] if position > 0 else None
        next_measure = group[position + 1][1] if position + 1 < len(group) else None

        anchor = line.interpolate(measure)
        tangent = _unit_tangent(line, measure)
        normal = (-tangent[1] * side, tangent[0] * side)
        tangent_min, tangent_max = _projection_span(seed_geometry, anchor, tangent)
        normal_min, normal_max = _projection_span(seed_geometry, anchor, normal)
        building_width = max(6.0, tangent_max - tangent_min)

        left = (measure - previous_measure) / 2.0 if previous_measure is not None else max(7.0, building_width / 2.0 + 4.0)
        right = (next_measure - measure) / 2.0 if next_measure is not None else max(7.0, building_width / 2.0 + 4.0)
        left = min(30.0, max(left, max(4.0, -tangent_min + 3.0)))
        right = min(30.0, max(right, max(4.0, tangent_max + 3.0)))
        depth = min(52.0, max(18.0, normal_max + 8.0))
        roadward = min(-1.0, normal_min - 2.0)

        def point(along: float, inward: float) -> tuple[float, float]:
            return (
                anchor.x + tangent[0] * along + normal[0] * inward,
                anchor.y + tangent[1] * along + normal[1] * inward,
            )

        strip = Polygon([
            point(-left, roadward),
            point(right, roadward),
            point(right, depth),
            point(-left, depth),
            point(-left, roadward),
        ])
        candidate = _clean_polygon(strip.intersection(block).intersection(owner))

        other_buildings = [
            geometry.buffer(2.0, join_style="mitre")
            for other_id, geometry in seeds
            if other_id != seed_id
        ]
        if other_buildings and not candidate.is_empty:
            candidate = _clean_polygon(
                candidate.difference(unary_union(other_buildings))
            )
        protected_seed = seed_geometry.intersection(block)
        candidate = _clean_polygon(candidate.union(protected_seed).intersection(owner))

        cells.append((seed_id, candidate if not candidate.is_empty else owner))

    return cells


def _cap_candidate_area(
    geometry: BaseGeometry,
    *,
    seed_geometry: BaseGeometry,
    block: BaseGeometry,
    road_corridor: BaseGeometry,
    max_area_m2: float,
    max_radius_m: float = 30.0,
) -> BaseGeometry:
    if geometry.is_empty or geometry.area <= max_area_m2:
        return geometry

    # In incompletely road-bounded imagery, raw Voronoi cells can stretch to
    # the raster edge. Keep oversized candidates local to their building seed,
    # while extending far enough to meet a nearby detected road edge when one
    # exists. Intersecting with the Voronoi cell preserves non-overlap.
    road_distance = (
        float(seed_geometry.distance(road_corridor))
        if not road_corridor.is_empty
        else max_radius_m
    )
    local_radius = min(max_radius_m, max(12.0, road_distance + 4.0))
    local = _clean_polygon(
        geometry.intersection(
            seed_geometry.buffer(local_radius, join_style="mitre")
        )
    )
    if local.is_empty:
        local = geometry
    if local.area <= max_area_m2:
        return _clean_polygon(local.intersection(block))

    factor = math.sqrt(max_area_m2 / max(float(local.area), 1e-9))
    scaled = affinity.scale(
        local,
        xfact=factor,
        yfact=factor,
        origin=(seed_geometry.centroid.x, seed_geometry.centroid.y),
    )
    return _clean_polygon(scaled.intersection(block))


def _split_open_area(
    geometry: BaseGeometry,
    *,
    max_area_m2: float,
    min_area_m2: float,
    max_depth: int = 8,
) -> list[Polygon]:
    """Split a large residual land region into simple review-sized pieces.

    This is deliberately geometry-only and never treated as authoritative
    ownership evidence. It exists so road-bounded vacant/open land is visible
    for human review instead of disappearing from the parcel workflow.
    """
    queue: list[tuple[Polygon, int]] = [
        (part, 0)
        for part in _polygon_parts(_clean_polygon(geometry))
        if part.area >= min_area_m2
    ]
    output: list[Polygon] = []

    while queue:
        part, depth = queue.pop(0)
        if part.area <= max_area_m2 or depth >= max_depth:
            output.append(part)
            continue

        min_x, min_y, max_x, max_y = part.bounds
        width, height = max_x - min_x, max_y - min_y
        padding = max(width, height, 1.0) + 5.0
        if width >= height:
            cut_x = (min_x + max_x) / 2.0
            cutter = LineString([
                (cut_x, min_y - padding),
                (cut_x, max_y + padding),
            ])
        else:
            cut_y = (min_y + max_y) / 2.0
            cutter = LineString([
                (min_x - padding, cut_y),
                (max_x + padding, cut_y),
            ])

        pieces = [
            value
            for value in _polygon_parts(shapely_split(part, cutter))
            if value.area >= min_area_m2
        ]
        if len(pieces) < 2:
            output.append(part)
            continue
        queue.extend((value, depth + 1) for value in pieces)

    return output


def generate_parcel_candidates(
    source_path: str | Path,
    *,
    buildings: Iterable[SpatialFeature],
    roads: Iterable[SpatialFeature],
    road_half_width_m: float = 4.5,
    min_block_area_m2: float = 120.0,
    min_parcel_area_m2: float = 60.0,
    max_single_seed_block_area_m2: float = 2500.0,
    single_seed_radius_m: float = 26.0,
    max_parcel_area_m2: float = 2400.0,
    min_open_area_m2: float = 25.0,
    min_open_candidate_area_m2: float = 140.0,
    max_open_area_m2: float = 1800.0,
    max_empty_block_area_m2: float = 5000.0,
    simplify_m: float = 0.20,
) -> ParcelCandidateResult:
    """Create building-aware, road-bounded preliminary plot polygons."""
    footprint_wgs84 = _raster_footprint_wgs84(source_path)
    metric_crs, to_metric, to_wgs84 = _metric_transformers(footprint_wgs84)
    project = lambda geometry: shapely_transform(to_metric.transform, geometry)
    unproject = lambda geometry: shapely_transform(to_wgs84.transform, geometry)

    footprint = _clean_polygon(project(footprint_wgs84))
    building_items = [
        SpatialFeature(item.id, _clean_polygon(project(item.geometry)))
        for item in buildings
        if item.geometry is not None and not item.geometry.is_empty
    ]
    road_items = [
        SpatialFeature(item.id, project(item.geometry))
        for item in roads
        if item.geometry is not None and not item.geometry.is_empty
    ]

    road_parts: list[BaseGeometry] = []
    for road in road_items:
        geometry = road.geometry
        if geometry.geom_type in {"Polygon", "MultiPolygon"}:
            road_parts.append(geometry)
        else:
            road_parts.append(geometry.buffer(road_half_width_m, cap_style="flat", join_style="round"))
    road_corridor = unary_union(road_parts) if road_parts else Polygon()
    land = _clean_polygon(footprint.difference(road_corridor)) if not road_corridor.is_empty else footprint
    blocks = sorted(
        [part for part in _polygon_parts(land) if part.area >= min_block_area_m2],
        key=lambda part: (-part.area, part.centroid.x, part.centroid.y),
    )

    raw_candidates: list[ParcelCandidate] = []
    skipped_blocks = 0
    for block_index, block in enumerate(blocks, start=1):
        seeds = [
            (building.id, building.geometry)
            for building in building_items
            if block.buffer(0.75).contains(building.geometry.centroid)
        ]
        if not seeds:
            skipped_blocks += 1
            continue

        if len(seeds) == 1 and block.area > max_single_seed_block_area_m2:
            seed_id, building_geometry = seeds[0]
            limited = _clean_polygon(
                block.intersection(building_geometry.buffer(single_seed_radius_m, join_style="mitre"))
            )
            cells = [(seed_id, limited)] if not limited.is_empty else []
        else:
            cells = _frontage_strip_cells(block, seeds, road_items)

        for seed_id, cell in cells:
            seed_building = next(geometry for item_id, geometry in seeds if item_id == seed_id)
            bounded = _cap_candidate_area(
                cell,
                seed_geometry=seed_building,
                block=block,
                road_corridor=road_corridor,
                max_area_m2=max_parcel_area_m2,
            )
            if bounded.is_empty:
                continue
            # Keep one plot candidate per building seed. If clipping creates
            # disconnected pieces, retain the piece that contains the seed.
            parts = _polygon_parts(bounded)
            if not parts:
                continue
            part = min(parts, key=lambda value: value.distance(seed_building.centroid))
            if part.area < min_parcel_area_m2:
                continue
            contained_buildings = tuple(
                sorted(
                    item_id
                    for item_id, geometry in seeds
                    if part.buffer(0.25).contains(geometry.centroid)
                )
            )
            crosses_building = not part.buffer(0.20).contains(seed_building)
            frontage = _frontage_length(part, road_corridor)
            warnings: list[str] = [
                "AI parcel candidate only; legal boundary requires cadastral/FMB or survey verification."
            ]
            if frontage < 1.0:
                warnings.append("No reliable road frontage was inferred.")
            if crosses_building:
                warnings.append("Candidate edge intersects or excludes part of the seed building; review required.")
            if len(contained_buildings) > 1:
                warnings.append("Candidate contains multiple detected buildings; subdivision may require review.")

            cleaned = part.simplify(simplify_m, preserve_topology=True) if simplify_m > 0 else part
            cleaned = _clean_polygon(cleaned)
            area = float(cleaned.area)
            if area < min_parcel_area_m2:
                continue
            confidence = _candidate_score(
                area_m2=area,
                road_frontage_m=frontage,
                building_count=len(contained_buildings),
                crosses_building=crosses_building,
                block_seed_count=len(seeds),
            )
            raw_candidates.append(
                ParcelCandidate(
                    geometry=_clean_polygon(unproject(cleaned)),
                    confidence=confidence,
                    building_ids=contained_buildings or (seed_id,),
                    road_frontage_m=frontage,
                    area_m2=area,
                    area_sqft=area * 10.7639104167,
                    block_index=block_index,
                    warnings=tuple(warnings),
                )
            )

    built_candidate_count = len(raw_candidates)
    assigned_building_ids = {
        building_id
        for candidate in raw_candidates
        for building_id in candidate.building_ids
    }
    open_candidate_count = 0
    fallback_building_candidate_count = 0
    excluded_open_block_count = 0
    coverage_target_area_m2 = 0.0

    # Fill the road-bounded remainder so vacant/open land does not silently
    # disappear from the map. These residual regions are intentionally lower
    # confidence and explicitly marked for review; they are not asserted to be
    # legal cadastral parcels.
    for block_index, block in enumerate(blocks, start=1):
        block_seed_items = [
            building
            for building in building_items
            if block.buffer(0.75).contains(building.geometry.centroid)
        ]
        block_frontage = _frontage_length(block, road_corridor)
        eligible_empty_block = (
            not block_seed_items
            and block.area <= max_empty_block_area_m2
            and block_frontage >= 5.0
        )
        if not block_seed_items and not eligible_empty_block:
            excluded_open_block_count += 1
            continue

        coverage_target_area_m2 += float(block.area)
        existing_metric = [
            project(candidate.geometry)
            for candidate in raw_candidates
            if candidate.block_index == block_index
        ]
        covered = unary_union(existing_metric) if existing_metric else Polygon()
        residual = _clean_polygon(
            block.difference(covered)
            if not covered.is_empty
            else block
        )

        def absorb_residual_into_neighbor(piece: BaseGeometry) -> bool:
            candidate_indices = [
                index
                for index, candidate in enumerate(raw_candidates)
                if candidate.block_index == block_index
            ]
            if not candidate_indices:
                return False

            ranked: list[tuple[float, float, int, BaseGeometry]] = []
            for index in candidate_indices:
                candidate_metric = project(raw_candidates[index].geometry)
                shared = float(candidate_metric.boundary.intersection(piece.boundary).length)
                distance = float(candidate_metric.distance(piece))
                ranked.append((shared, -distance, index, candidate_metric))
            shared, negative_distance, index, candidate_metric = max(
                ranked,
                key=lambda item: (item[0], item[1]),
            )
            if shared < 0.05 and -negative_distance > 0.05:
                return False

            merged = _clean_polygon(candidate_metric.union(piece))
            merged_parts = _polygon_parts(merged)
            if len(merged_parts) != 1:
                return False
            merged_polygon = merged_parts[0]
            candidate = raw_candidates[index]
            raw_candidates[index] = replace(
                candidate,
                geometry=_clean_polygon(unproject(merged_polygon)),
                road_frontage_m=_frontage_length(merged_polygon, road_corridor),
                area_m2=float(merged_polygon.area),
                area_sqft=float(merged_polygon.area) * 10.7639104167,
            )
            return True

        for residual_part in _polygon_parts(residual):
            if residual_part.area < min_open_area_m2:
                absorb_residual_into_neighbor(residual_part)
                continue
            for open_part in _split_open_area(
                residual_part,
                max_area_m2=max_open_area_m2,
                min_area_m2=min_open_area_m2,
            ):
                cleaned_open = (
                    open_part.simplify(simplify_m, preserve_topology=True)
                    if simplify_m > 0
                    else open_part
                )
                cleaned_open = _clean_polygon(cleaned_open.intersection(block))
                area = float(cleaned_open.area)
                if area < min_open_area_m2:
                    continue

                unassigned_here = tuple(
                    sorted(
                        building.id
                        for building in block_seed_items
                        if building.id not in assigned_building_ids
                        and cleaned_open.buffer(0.25).contains(building.geometry.centroid)
                    )
                )
                if (
                    not unassigned_here
                    and area < min_open_candidate_area_m2
                    and absorb_residual_into_neighbor(cleaned_open)
                ):
                    continue

                frontage = _frontage_length(cleaned_open, road_corridor)
                warnings: list[str] = [
                    "AI parcel candidate only; legal boundary requires cadastral/FMB or survey verification."
                ]
                if unassigned_here:
                    warnings.append(
                        "Fallback review candidate contains a building seed that could not form a standard frontage plot."
                    )
                    confidence = 0.62 if frontage >= 1.0 else 0.52
                    fallback_building_candidate_count += 1
                    assigned_building_ids.update(unassigned_here)
                else:
                    warnings.append(
                        "Vacant/open road-block remainder inferred from geometry only; ownership subdivision is unresolved."
                    )
                    confidence = 0.56 if frontage >= 3.0 else 0.42
                    open_candidate_count += 1
                if frontage < 1.0:
                    warnings.append(
                        "No reliable road frontage was inferred; human review is required."
                    )

                raw_candidates.append(
                    ParcelCandidate(
                        geometry=_clean_polygon(unproject(cleaned_open)),
                        confidence=confidence,
                        building_ids=unassigned_here,
                        road_frontage_m=frontage,
                        area_m2=area,
                        area_sqft=area * 10.7639104167,
                        block_index=block_index,
                        warnings=tuple(warnings),
                    )
                )

    candidate_list = sorted(
        raw_candidates,
        key=lambda item: (
            item.block_index,
            round(item.geometry.centroid.y, 8),
            round(item.geometry.centroid.x, 8),
        ),
    )

    # Parcel persistence intentionally rejects disconnected MultiPolygons.
    # Geometry repair can occasionally split a candidate into multiple islands;
    # keep the island containing a building seed as that plot and expose any
    # detached remainder as its own lower-confidence open-land review region.
    building_by_id = {item.id: item.geometry for item in building_items}
    normalized_candidates: list[ParcelCandidate] = []
    multipart_split_count = 0
    for candidate in candidate_list:
        parts = _polygon_parts(candidate.geometry)
        if len(parts) <= 1:
            normalized_candidates.append(candidate)
            continue

        multipart_split_count += 1
        main_index: int | None = None
        if candidate.building_ids:
            seed_metric = unary_union([
                building_by_id[building_id]
                for building_id in candidate.building_ids
                if building_id in building_by_id
            ])
            if not seed_metric.is_empty:
                main_index = min(
                    range(len(parts)),
                    key=lambda index: project(parts[index]).distance(seed_metric.centroid),
                )

        for index, part in enumerate(parts):
            metric_part = _clean_polygon(project(part))
            if metric_part.is_empty or metric_part.area < min_open_area_m2:
                continue
            frontage = _frontage_length(metric_part, road_corridor)
            if main_index is not None and index == main_index:
                normalized_candidates.append(
                    replace(
                        candidate,
                        geometry=part,
                        road_frontage_m=frontage,
                        area_m2=float(metric_part.area),
                        area_sqft=float(metric_part.area) * 10.7639104167,
                    )
                )
                continue

            open_warnings = (
                "AI parcel candidate only; legal boundary requires cadastral/FMB or survey verification.",
                "Detached/open geometry was split from a multipart candidate and requires human review.",
            )
            normalized_candidates.append(
                ParcelCandidate(
                    geometry=part,
                    confidence=min(candidate.confidence, 0.42),
                    building_ids=(),
                    road_frontage_m=frontage,
                    area_m2=float(metric_part.area),
                    area_sqft=float(metric_part.area) * 10.7639104167,
                    block_index=candidate.block_index,
                    warnings=open_warnings,
                )
            )

    candidate_list = sorted(
        normalized_candidates,
        key=lambda item: (
            item.block_index,
            round(item.geometry.centroid.y, 8),
            round(item.geometry.centroid.x, 8),
        ),
    )

    # Projection/repair can introduce sub-square-metre numerical overlaps even
    # when the metric ownership cells only share an edge. Resolve only these
    # micro-overlaps; substantive overlaps remain visible for review rather
    # than being silently altered.
    micro_overlap_cleanup_count = 0
    for left_index in range(len(candidate_list)):
        for right_index in range(left_index + 1, len(candidate_list)):
            left_metric = project(candidate_list[left_index].geometry)
            right_metric = project(candidate_list[right_index].geometry)
            overlap = left_metric.intersection(right_metric)
            if overlap.is_empty or overlap.area <= 1e-5 or overlap.area > 1.0:
                continue

            left_seed = unary_union([
                building_by_id[building_id]
                for building_id in candidate_list[left_index].building_ids
                if building_id in building_by_id
            ])
            right_seed = unary_union([
                building_by_id[building_id]
                for building_id in candidate_list[right_index].building_ids
                if building_id in building_by_id
            ])
            protects_left = not left_seed.is_empty and overlap.intersects(left_seed)
            protects_right = not right_seed.is_empty and overlap.intersects(right_seed)

            loser_index = (
                right_index
                if protects_left and not protects_right
                else left_index
                if protects_right and not protects_left
                else right_index
            )
            loser = candidate_list[loser_index]
            loser_metric = project(loser.geometry)
            trimmed = _clean_polygon(
                loser_metric.difference(overlap.buffer(0.02))
            )
            parts = _polygon_parts(trimmed)
            if not parts:
                continue

            seed_geometry = unary_union([
                building_by_id[building_id]
                for building_id in loser.building_ids
                if building_id in building_by_id
            ])
            if seed_geometry.is_empty:
                kept = max(parts, key=lambda value: value.area)
            else:
                kept = min(parts, key=lambda value: value.distance(seed_geometry.centroid))
                if not kept.buffer(0.20).contains(seed_geometry):
                    continue

            cleaned_wgs84 = _clean_polygon(unproject(kept))
            candidate_list[loser_index] = replace(
                loser,
                geometry=cleaned_wgs84,
                area_m2=float(kept.area),
                area_sqft=float(kept.area) * 10.7639104167,
            )
            micro_overlap_cleanup_count += 1

    boundary_evidence_results, boundary_evidence_metrics = analyze_candidate_boundaries(
        source_path,
        geometries_wgs84=[candidate.geometry for candidate in candidate_list],
        roads_wgs84=[unproject(item.geometry) for item in road_items],
        metric_crs=metric_crs,
        road_half_width_m=road_half_width_m,
    )
    candidate_list = [
        replace(candidate, boundary_evidence=evidence)
        for candidate, evidence in zip(candidate_list, boundary_evidence_results)
    ]

    candidates = tuple(candidate_list)
    covered_metric = unary_union([
        project(candidate.geometry)
        for candidate in candidates
    ]) if candidates else Polygon()
    covered_area_m2 = float(covered_metric.area) if not covered_metric.is_empty else 0.0
    land_coverage_ratio = (
        min(1.0, covered_area_m2 / coverage_target_area_m2)
        if coverage_target_area_m2 > 0
        else 0.0
    )
    final_building_candidate_count = sum(
        1 for candidate in candidates if candidate.building_ids
    )
    final_open_area_candidate_count = len(candidates) - final_building_candidate_count

    return ParcelCandidateResult(
        candidates=candidates,
        processing_parameters={
            "engine": "road-block-full-coverage-v3",
            "model_version": MODEL_VERSION,
            "metric_crs": metric_crs.to_string(),
            "road_half_width_m": road_half_width_m,
            "min_block_area_m2": min_block_area_m2,
            "min_parcel_area_m2": min_parcel_area_m2,
            "max_parcel_area_m2": max_parcel_area_m2,
            "min_open_area_m2": min_open_area_m2,
            "min_open_candidate_area_m2": min_open_candidate_area_m2,
            "max_open_area_m2": max_open_area_m2,
            "block_count": len(blocks),
            "skipped_blocks_without_buildings": skipped_blocks,
            "excluded_open_block_count": excluded_open_block_count,
            "building_seed_count": len(building_items),
            "road_feature_count": len(road_items),
            "candidate_count": len(candidates),
            "frontage_building_candidate_count": built_candidate_count,
            "fallback_building_candidate_count": fallback_building_candidate_count,
            "building_associated_candidate_count": final_building_candidate_count,
            "open_area_candidate_count": final_open_area_candidate_count,
            "multipart_split_count": multipart_split_count,
            "unassigned_building_seed_count": max(0, len(building_items) - len(assigned_building_ids)),
            "coverage_target_area_m2": coverage_target_area_m2,
            "covered_area_m2": covered_area_m2,
            "land_coverage_ratio": land_coverage_ratio,
            "micro_overlap_cleanup_count": micro_overlap_cleanup_count,
            **boundary_evidence_metrics,
        },
    )

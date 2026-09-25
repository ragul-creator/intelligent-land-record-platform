"""Deterministic conversion of road probability masks to preliminary centerlines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
from affine import Affine
from pyproj import CRS
from shapely.geometry import LineString
from skimage.morphology import (
    binary_closing,
    disk,
    remove_small_objects,
    skeletonize,
)

from ai.geoai.features.validation import length_metres
from ai.geoai.polygonization.geometry import crs_to_string


_NEIGHBOUR_OFFSETS = (
    (-1, -1),
    (-1, 0),
    (-1, 1),
    (0, -1),
    (0, 1),
    (1, -1),
    (1, 0),
    (1, 1),
)


@dataclass(frozen=True)
class RoadVectorizationConfig:
    threshold: float = 0.5
    min_component_pixels: int = 16

    # Minimum number of skeleton pixels required for a graph path.
    # This suppresses short spurs/noise after skeletonization.
    min_path_pixels: int = 20

    # Small morphological closing before skeletonization helps bridge
    # tiny segmentation gaps without aggressively joining nearby roads.
    closing_radius: int = 1

    simplify_tolerance: float = 0.0


@dataclass(frozen=True)
class RoadVectorFeature:
    geometry: LineString
    confidence: float | None
    model_version: str
    length_m: float | None
    processed_at: str


@dataclass(frozen=True)
class RoadVectorizationResult:
    features: tuple[RoadVectorFeature, ...]
    source_crs: str
    processed_at: str
    processing_parameters: dict[str, float]


def _neighbours(
    pixel: tuple[int, int],
    skeleton: np.ndarray,
) -> list[tuple[int, int]]:
    row, col = pixel
    height, width = skeleton.shape

    neighbours: list[tuple[int, int]] = []

    for row_offset, col_offset in _NEIGHBOUR_OFFSETS:
        next_row = row + row_offset
        next_col = col + col_offset

        if (
            0 <= next_row < height
            and 0 <= next_col < width
            and skeleton[next_row, next_col]
        ):
            neighbours.append((next_row, next_col))

    return neighbours


def _edge_key(
    first: tuple[int, int],
    second: tuple[int, int],
) -> tuple[tuple[int, int], tuple[int, int]]:
    return tuple(sorted((first, second)))


def _neighbour_count(skeleton: np.ndarray) -> np.ndarray:
    """Return 8-connected skeleton degree for every pixel."""
    padded = np.pad(
        skeleton.astype(np.uint8),
        1,
        mode="constant",
        constant_values=0,
    )

    height, width = skeleton.shape
    count = np.zeros((height, width), dtype=np.uint8)

    for row_offset, col_offset in _NEIGHBOUR_OFFSETS:
        row_start = 1 + row_offset
        col_start = 1 + col_offset

        count += padded[
            row_start : row_start + height,
            col_start : col_start + width,
        ]

    return count


def _trace_paths(
    skeleton: np.ndarray,
    *,
    min_path_pixels: int,
) -> list[list[tuple[int, int]]]:
    """
    Trace skeleton graph edges between endpoints/junctions.

    Pixels with degree != 2 are graph nodes. Degree-2 pixels are followed
    until another node is reached. Closed loops are handled separately.
    """
    if not skeleton.any():
        return []

    degree = _neighbour_count(skeleton)

    node_mask = skeleton & (degree != 2)

    nodes = sorted(
        map(tuple, np.argwhere(node_mask))
    )
    node_set = set(nodes)

    visited_edges: set[
        tuple[tuple[int, int], tuple[int, int]]
    ] = set()

    paths: list[list[tuple[int, int]]] = []

    # Trace paths that begin at an endpoint or junction.
    for node in nodes:
        for next_pixel in _neighbours(node, skeleton):
            first_edge = _edge_key(node, next_pixel)

            if first_edge in visited_edges:
                continue

            path = [node]

            previous = node
            current = next_pixel

            visited_edges.add(first_edge)

            while True:
                path.append(current)

                if current in node_set and current != node:
                    break

                candidates = [
                    candidate
                    for candidate in _neighbours(current, skeleton)
                    if candidate != previous
                ]

                if not candidates:
                    break

                unvisited = [
                    candidate
                    for candidate in candidates
                    if _edge_key(current, candidate)
                    not in visited_edges
                ]

                if not unvisited:
                    break

                next_pixel = unvisited[0]

                visited_edges.add(
                    _edge_key(current, next_pixel)
                )

                previous, current = current, next_pixel

            if len(path) >= min_path_pixels:
                paths.append(path)

    # Handle closed loops containing only degree-2 pixels.
    skeleton_pixels = sorted(
        map(tuple, np.argwhere(skeleton))
    )

    for start in skeleton_pixels:
        for next_pixel in _neighbours(start, skeleton):
            first_edge = _edge_key(start, next_pixel)

            if first_edge in visited_edges:
                continue

            path = [start]

            previous = start
            current = next_pixel

            visited_edges.add(first_edge)

            while True:
                path.append(current)

                candidates = [
                    candidate
                    for candidate in _neighbours(current, skeleton)
                    if candidate != previous
                ]

                unvisited = [
                    candidate
                    for candidate in candidates
                    if _edge_key(current, candidate)
                    not in visited_edges
                ]

                if not unvisited:
                    break

                next_pixel = unvisited[0]

                visited_edges.add(
                    _edge_key(current, next_pixel)
                )

                previous, current = current, next_pixel

                if current == start:
                    path.append(start)
                    break

            if len(path) >= min_path_pixels:
                paths.append(path)

    return paths


def _line_from_path(
    path: list[tuple[int, int]],
    transform: Affine,
    tolerance: float,
) -> LineString | None:
    points: list[tuple[float, float]] = []

    for row, col in path:
        x, y = transform @ (
            float(col) + 0.5,
            float(row) + 0.5,
        )

        point = (float(x), float(y))

        if not points or point != points[-1]:
            points.append(point)

    if len(points) < 2 or len(set(points)) < 2:
        return None

    line = LineString(points)

    if tolerance > 0:
        line = line.simplify(
            tolerance,
            preserve_topology=True,
        )

    if (
        line.is_empty
        or not line.is_valid
        or line.length <= 0
    ):
        return None

    return line


def vectorize_roads(
    probability: np.ndarray,
    *,
    transform: Affine,
    crs: CRS | str | None,
    model_version: str,
    config: RoadVectorizationConfig = RoadVectorizationConfig(),
) -> RoadVectorizationResult:
    if crs is None:
        raise ValueError(
            "Road vectorization requires georeferenced imagery with a CRS."
        )

    if (
        probability.ndim != 2
        or not 0 <= config.threshold <= 1
        or config.min_component_pixels < 1
        or config.min_path_pixels < 2
        or config.closing_radius < 0
    ):
        raise ValueError(
            "Road probability mask or vectorization configuration is invalid."
        )

    source_crs = crs_to_string(crs)

    processed_at = (
        datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )

    binary_mask = (
        np.isfinite(probability)
        & (probability >= config.threshold)
    )

    # Remove tiny disconnected segmentation responses before they can
    # generate skeleton branches.
    binary_mask = remove_small_objects(
        binary_mask,
        min_size=config.min_component_pixels,
        connectivity=2,
    )

    # Bridge only very small gaps in the road mask.
    if config.closing_radius > 0:
        binary_mask = binary_closing(
            binary_mask,
            footprint=disk(config.closing_radius),
        )

    skeleton = skeletonize(binary_mask)

    paths = _trace_paths(
        skeleton,
        min_path_pixels=config.min_path_pixels,
    )

    features: list[RoadVectorFeature] = []

    for path in paths:
        line = _line_from_path(
            path,
            transform,
            config.simplify_tolerance,
        )

        if line is None:
            continue

        pixels = np.asarray(path, dtype=np.int64)

        confidence = float(
            np.mean(
                probability[
                    pixels[:, 0],
                    pixels[:, 1],
                ]
            )
        )

        features.append(
            RoadVectorFeature(
                geometry=line,
                confidence=confidence,
                model_version=model_version,
                length_m=length_metres(
                    line,
                    source_crs,
                ),
                processed_at=processed_at,
            )
        )

    return RoadVectorizationResult(
        features=tuple(features),
        source_crs=source_crs,
        processed_at=processed_at,
        processing_parameters={
            "threshold": config.threshold,
            "min_component_pixels": float(
                config.min_component_pixels
            ),
            "min_path_pixels": float(
                config.min_path_pixels
            ),
            "closing_radius": float(
                config.closing_radius
            ),
            "simplify_tolerance": config.simplify_tolerance,
        },
    )

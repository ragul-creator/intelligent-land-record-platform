"""Deterministic conversion of binary road masks to preliminary centerlines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable

import numpy as np
from affine import Affine
from pyproj import CRS
from shapely.geometry import LineString

from ai.geoai.features.validation import length_metres
from ai.geoai.polygonization.geometry import crs_to_string


@dataclass(frozen=True)
class RoadVectorizationConfig:
    threshold: float = 0.5
    min_component_pixels: int = 16
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


def _components(mask: np.ndarray) -> Iterable[np.ndarray]:
    visited = np.zeros(mask.shape, dtype=bool)
    height, width = mask.shape
    for row, col in zip(*np.nonzero(mask)):
        if visited[row, col]:
            continue
        stack, points = [(int(row), int(col))], []
        visited[row, col] = True
        while stack:
            current_row, current_col = stack.pop()
            points.append((current_row, current_col))
            for next_row in range(max(0, current_row - 1), min(height, current_row + 2)):
                for next_col in range(max(0, current_col - 1), min(width, current_col + 2)):
                    if mask[next_row, next_col] and not visited[next_row, next_col]:
                        visited[next_row, next_col] = True
                        stack.append((next_row, next_col))
        yield np.asarray(points, dtype=np.float64)


def _centerline(component: np.ndarray, transform: Affine, tolerance: float) -> LineString | None:
    """Derive a stable centerline by grouping component pixels along its principal axis."""
    if len(component) < 2:
        return None
    centered = component - component.mean(axis=0)
    _, _, vectors = np.linalg.svd(centered, full_matrices=False)
    axis = vectors[0]
    projection = centered @ axis
    buckets = np.rint(projection).astype(int)
    points = []
    for bucket in sorted(set(buckets)):
        row, col = component[buckets == bucket].mean(axis=0)
        x, y = transform @ (float(col) + 0.5, float(row) + 0.5)
        points.append((x, y))
    if len(points) < 2 or len(set(points)) < 2:
        return None
    line = LineString(points)
    if tolerance > 0:
        line = line.simplify(tolerance, preserve_topology=True)
    return line if not line.is_empty and line.is_valid and line.length > 0 else None


def vectorize_roads(probability: np.ndarray, *, transform: Affine, crs: CRS | str | None, model_version: str, config: RoadVectorizationConfig = RoadVectorizationConfig()) -> RoadVectorizationResult:
    if crs is None:
        raise ValueError("Road vectorization requires georeferenced imagery with a CRS.")
    if probability.ndim != 2 or not 0 <= config.threshold <= 1 or config.min_component_pixels < 1:
        raise ValueError("Road probability mask or vectorization configuration is invalid.")
    source_crs = crs_to_string(crs)
    processed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    features = []
    for component in _components(np.isfinite(probability) & (probability >= config.threshold)):
        if len(component) < config.min_component_pixels:
            continue
        line = _centerline(component, transform, config.simplify_tolerance)
        if line is None:
            continue
        pixels = component.astype(int)
        confidence = float(probability[pixels[:, 0], pixels[:, 1]].mean())
        features.append(RoadVectorFeature(line, confidence, model_version, length_metres(line, source_crs), processed_at))
    return RoadVectorizationResult(tuple(features), source_crs, processed_at, {"threshold": config.threshold, "min_component_pixels": float(config.min_component_pixels), "simplify_tolerance": config.simplify_tolerance})

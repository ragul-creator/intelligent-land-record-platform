"""Conservative visible-boundary evidence for preliminary parcel edges.

This module scores what the imagery visibly supports. It does not infer legal
ownership and deliberately avoids claiming a wall/fence when only a generic
linear image edge is observable.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable

import numpy as np
from pyproj import CRS, Transformer
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import Affine
from shapely.geometry import LineString, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as shapely_transform, unary_union


BOUNDARY_EVIDENCE_MODEL_VERSION = "visible-boundary-evidence-v1"
@dataclass(frozen=True)
class BoundaryEvidenceSegment:
    geometry: LineString
    evidence_type: str
    confidence: float
    length_m: float
    support_fraction: float


@dataclass(frozen=True)
class _RasterEvidence:
    gradient: np.ndarray
    vegetation_gradient: np.ndarray
    transform: Affine
    crs: CRS
    gradient_threshold: float
    gradient_reference: float
    vegetation_threshold: float


def _polygon_parts(geometry: BaseGeometry) -> list[Polygon]:
    if geometry.is_empty:
        return []
    if isinstance(geometry, Polygon):
        return [geometry]
    return [
        part
        for part in getattr(geometry, "geoms", ())
        if isinstance(part, Polygon) and not part.is_empty
    ]
def _normalise_rgb(array: np.ndarray) -> np.ndarray:
    rgb = np.moveaxis(array.astype("float32"), 0, -1)
    output = np.zeros_like(rgb, dtype="float32")
    for channel in range(rgb.shape[-1]):
        values = rgb[..., channel]
        finite = values[np.isfinite(values)]
        if finite.size == 0:
            continue
        low, high = np.percentile(finite, [2.0, 98.0])
        if high <= low + 1e-6:
            continue
        output[..., channel] = np.clip((values - low) / (high - low), 0.0, 1.0)
    return output


def _load_raster_evidence(source_path: str | Path, *, max_dimension: int = 1600) -> _RasterEvidence:
    with rasterio.open(source_path) as dataset:
        if dataset.crs is None:
            raise ValueError("Boundary evidence requires georeferenced imagery.")
        scale = min(1.0, max_dimension / max(dataset.width, dataset.height))
        width = max(1, int(round(dataset.width * scale)))
        height = max(1, int(round(dataset.height * scale)))
        indexes = list(range(1, min(dataset.count, 3) + 1))
        array = dataset.read(
            indexes,
            out_shape=(len(indexes), height, width),
            resampling=Resampling.bilinear,
        )
        if array.shape[0] == 1:
            array = np.repeat(array, 3, axis=0)
        elif array.shape[0] == 2:
            array = np.concatenate([array, array[1:2]], axis=0)
        transform = dataset.transform @ Affine.scale(
            dataset.width / width,
            dataset.height / height,
        )
        crs = CRS.from_user_input(dataset.crs)

    rgb = _normalise_rgb(array[:3])
    gray = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
    grad_y, grad_x = np.gradient(gray)
    gradient = np.hypot(grad_x, grad_y).astype("float32")

    vegetation = rgb[..., 1] - 0.5 * (rgb[..., 0] + rgb[..., 2])
    veg_y, veg_x = np.gradient(vegetation)
    vegetation_gradient = np.hypot(veg_x, veg_y).astype("float32")

    finite_gradient = gradient[np.isfinite(gradient)]
    finite_vegetation = vegetation_gradient[np.isfinite(vegetation_gradient)]
    gradient_threshold = max(
        0.06,
        float(np.percentile(finite_gradient, 78.0)) if finite_gradient.size else 0.06,
    )
    gradient_reference = max(
        gradient_threshold,
        float(np.percentile(finite_gradient, 95.0)) if finite_gradient.size else 0.12,
    )
    vegetation_threshold = max(
        0.05,
        float(np.percentile(finite_vegetation, 80.0)) if finite_vegetation.size else 0.05,
    )
    return _RasterEvidence(
        gradient=gradient,
        vegetation_gradient=vegetation_gradient,
        transform=transform,
        crs=crs,
        gradient_threshold=gradient_threshold,
        gradient_reference=gradient_reference,
        vegetation_threshold=vegetation_threshold,
    )


def _local_max(surface: np.ndarray, row: int, col: int, radius: int = 1) -> float:
    row0, row1 = max(0, row - radius), min(surface.shape[0], row + radius + 1)
    col0, col1 = max(0, col - radius), min(surface.shape[1], col + radius + 1)
    if row0 >= row1 or col0 >= col1:
        return 0.0
    values = surface[row0:row1, col0:col1]
    return float(np.nanmax(values)) if values.size else 0.0
def _sample_image_support(
    segment_wgs84: LineString,
    segment_length_m: float,
    evidence: _RasterEvidence,
) -> tuple[float, float, float]:
    to_raster = Transformer.from_crs("EPSG:4326", evidence.crs, always_xy=True)
    sample_count = min(96, max(5, int(math.ceil(segment_length_m / 0.75)) + 1))
    gradient_values: list[float] = []
    vegetation_values: list[float] = []
    inverse = ~evidence.transform

    for index in range(sample_count):
        fraction = index / max(1, sample_count - 1)
        point = segment_wgs84.interpolate(fraction, normalized=True)
        x, y = to_raster.transform(float(point.x), float(point.y))
        col_f, row_f = inverse @ (x, y)
        row, col = int(round(row_f)), int(round(col_f))
        if not (0 <= row < evidence.gradient.shape[0] and 0 <= col < evidence.gradient.shape[1]):
            continue
        gradient_values.append(_local_max(evidence.gradient, row, col))
        vegetation_values.append(_local_max(evidence.vegetation_gradient, row, col))

    if not gradient_values:
        return 0.0, 0.0, 0.0
    gradients = np.asarray(gradient_values, dtype="float32")
    vegetation = np.asarray(vegetation_values, dtype="float32")
    support_fraction = float(np.mean(gradients >= evidence.gradient_threshold))
    strength = float(
        np.mean(np.clip(gradients / max(evidence.gradient_reference, 1e-6), 0.0, 1.0))
    )
    vegetation_support = float(np.mean(vegetation >= evidence.vegetation_threshold))
    return support_fraction, strength, vegetation_support


def _road_corridor(
    roads_wgs84: Iterable[BaseGeometry],
    to_metric: Transformer,
    road_half_width_m: float,
) -> BaseGeometry:
    parts: list[BaseGeometry] = []
    for road in roads_wgs84:
        metric = shapely_transform(to_metric.transform, road)
        if metric.is_empty:
            continue
        if metric.geom_type in {"Polygon", "MultiPolygon"}:
            parts.append(metric)
        else:
            parts.append(
                metric.buffer(
                    road_half_width_m,
                    cap_style="flat",
                    join_style="round",
                )
            )
    return unary_union(parts) if parts else Polygon()
def analyze_candidate_boundaries(
    source_path: str | Path,
    *,
    geometries_wgs84: Iterable[BaseGeometry],
    roads_wgs84: Iterable[BaseGeometry],
    metric_crs: CRS,
    road_half_width_m: float = 4.5,
) -> tuple[list[tuple[BoundaryEvidenceSegment, ...]], dict[str, object]]:
    """Assign conservative evidence labels to each exterior parcel edge."""
    evidence = _load_raster_evidence(source_path)
    to_metric = Transformer.from_crs("EPSG:4326", metric_crs, always_xy=True)
    road_corridor = _road_corridor(roads_wgs84, to_metric, road_half_width_m)
    road_zone = (
        road_corridor.boundary.buffer(0.85)
        if not road_corridor.is_empty
        else Polygon()
    )

    all_results: list[tuple[BoundaryEvidenceSegment, ...]] = []
    type_lengths: defaultdict[str, float] = defaultdict(float)
    type_counts: defaultdict[str, int] = defaultdict(int)

    for geometry in geometries_wgs84:
        segments: list[BoundaryEvidenceSegment] = []
        for polygon in _polygon_parts(geometry):
            coordinates = list(polygon.exterior.coords)
            for start, end in zip(coordinates, coordinates[1:]):
                edge = LineString([start, end])
                metric_edge = shapely_transform(to_metric.transform, edge)
                length_m = float(metric_edge.length)
                if length_m < 0.05:
                    continue

                road_fraction = 0.0
                if not road_zone.is_empty:
                    road_fraction = min(
                        1.0,
                        float(metric_edge.intersection(road_zone).length) / length_m,
                    )

                if road_fraction >= 0.35:
                    evidence_type = "ROAD_EDGE"
                    confidence = min(0.98, 0.82 + 0.16 * road_fraction)
                    support_fraction = road_fraction
                else:
                    support_fraction, strength, vegetation_support = _sample_image_support(
                        edge,
                        length_m,
                        evidence,
                    )
                    if support_fraction >= 0.55 and strength >= 0.28:
                        if vegetation_support >= 0.55:
                            evidence_type = "VEGETATION_EDGE"
                            confidence = min(
                                0.78,
                                0.48 + 0.20 * support_fraction + 0.10 * strength,
                            )
                        else:
                            evidence_type = "VISIBLE_LINEAR_EDGE"
                            confidence = min(
                                0.82,
                                0.50 + 0.22 * support_fraction + 0.10 * strength,
                            )
                    else:
                        evidence_type = "GEOMETRY_ONLY"
                        confidence = min(0.44, 0.26 + 0.16 * support_fraction)

                segment = BoundaryEvidenceSegment(
                    geometry=edge,
                    evidence_type=evidence_type,
                    confidence=round(float(confidence), 4),
                    length_m=length_m,
                    support_fraction=round(float(support_fraction), 4),
                )
                segments.append(segment)
                type_lengths[evidence_type] += length_m
                type_counts[evidence_type] += 1
        all_results.append(tuple(segments))
    total_length = sum(type_lengths.values())
    supported_length = sum(
        length
        for evidence_type, length in type_lengths.items()
        if evidence_type != "GEOMETRY_ONLY"
    )
    metrics: dict[str, object] = {
        "boundary_evidence_model_version": BOUNDARY_EVIDENCE_MODEL_VERSION,
        "boundary_edge_count": int(sum(type_counts.values())),
        "boundary_supported_fraction": (
            float(supported_length / total_length) if total_length > 0 else 0.0
        ),
        "boundary_evidence_type_counts": dict(type_counts),
        "boundary_evidence_type_lengths_m": {
            key: round(value, 3) for key, value in type_lengths.items()
        },
        "gradient_threshold": round(evidence.gradient_threshold, 6),
        "vegetation_gradient_threshold": round(evidence.vegetation_threshold, 6),
    }
    return all_results, metrics


def summarise_boundary_evidence(
    segments: Iterable[BoundaryEvidenceSegment],
) -> dict[str, object]:
    items = list(segments)
    lengths: defaultdict[str, float] = defaultdict(float)
    weighted_confidence: defaultdict[str, float] = defaultdict(float)
    counts: defaultdict[str, int] = defaultdict(int)
    for segment in items:
        lengths[segment.evidence_type] += segment.length_m
        weighted_confidence[segment.evidence_type] += segment.confidence * segment.length_m
        counts[segment.evidence_type] += 1

    total = sum(lengths.values())
    supported = sum(
        length
        for evidence_type, length in lengths.items()
        if evidence_type != "GEOMETRY_ONLY"
    )
    by_type = {
        evidence_type: {
            "segment_count": counts[evidence_type],
            "length_m": round(length, 3),
            "fraction": round(length / total, 4) if total > 0 else 0.0,
            "mean_confidence": round(
                weighted_confidence[evidence_type] / length,
                4,
            ) if length > 0 else 0.0,
        }
        for evidence_type, length in sorted(lengths.items())
    }
    primary = max(lengths, key=lengths.get) if lengths else "GEOMETRY_ONLY"
    return {
        "primary_type": primary,
        "edge_count": len(items),
        "total_length_m": round(total, 3),
        "supported_fraction": round(supported / total, 4) if total > 0 else 0.0,
        "by_type": by_type,
    }

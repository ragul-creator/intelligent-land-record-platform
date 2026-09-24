"""Vectorize C.2 building masks into preliminary pixel- or world-space GIS features."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import numpy as np
import rasterio
from affine import Affine
from PIL import Image
from pyproj import CRS
from rasterio.features import geometry_mask, shapes
from shapely.geometry import mapping, shape
from shapely.geometry.base import BaseGeometry

from ai.geoai.polygonization.geometry import area_square_feet, area_square_metres, clean_polygon, crs_to_string, to_wgs84


CoordinateSpace = Literal["WORLD", "PIXEL"]


@dataclass(frozen=True)
class VectorizationConfig:
    threshold: float = 0.5
    min_area: float = 0.0
    simplify_tolerance: float = 0.0
    default_confidence: float | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1.")
        if self.min_area < 0:
            raise ValueError("min_area must be zero or greater.")
        if self.simplify_tolerance < 0:
            raise ValueError("simplify_tolerance must be zero or greater.")
        if self.default_confidence is not None and not 0.0 <= self.default_confidence <= 1.0:
            raise ValueError("default_confidence must be between 0 and 1 when supplied.")


@dataclass(frozen=True)
class BuildingFeature:
    geometry: BaseGeometry
    confidence: float | None
    area_m2: float | None
    area_sqft: float | None
    coordinate_space: CoordinateSpace
    crs: str | None
    model_version: str
    source_image: str | None
    source_mask: str | None
    processed_at: str

    def properties(self, *, exported_crs: str | None = None, source_crs: str | None = None) -> dict[str, object]:
        return {
            "feature_type": "building",
            "class": "building",
            "confidence": self.confidence,
            "area_m2": self.area_m2,
            "area_sqft": self.area_sqft,
            "coordinate_space": self.coordinate_space,
            "crs": exported_crs if exported_crs is not None else self.crs,
            "source_crs": source_crs,
            "model_version": self.model_version,
            "source_image": self.source_image,
            "source_mask": self.source_mask,
            "processed_at": self.processed_at,
            "status": "AI_PRELIMINARY",
            "verification_status": "UNVERIFIED",
        }


@dataclass(frozen=True)
class VectorizationResult:
    features: tuple[BuildingFeature, ...]
    coordinate_space: CoordinateSpace
    source_crs: str | None
    processed_at: str
    processing_parameters: dict[str, float | None]

    def to_feature_collection(self) -> dict[str, object]:
        """Export RFC 7946-style WGS84 GeoJSON, retaining source CRS as metadata."""
        geojson_features: list[dict[str, object]] = []
        for feature in self.features:
            geometry = feature.geometry
            exported_crs = None
            if self.coordinate_space == "WORLD" and self.source_crs is not None:
                geometry = to_wgs84(geometry, self.source_crs)
                exported_crs = "EPSG:4326"
            geojson_features.append(
                {
                    "type": "Feature",
                    "geometry": mapping(geometry),
                    "properties": feature.properties(exported_crs=exported_crs, source_crs=self.source_crs),
                }
            )
        return {
            "type": "FeatureCollection",
            "features": geojson_features,
            "metadata": {
                "coordinate_space": self.coordinate_space,
                "source_crs": self.source_crs,
                "export_crs": "EPSG:4326" if self.coordinate_space == "WORLD" else None,
                "processed_at": self.processed_at,
                "processing_parameters": self.processing_parameters,
                "status": "AI_PRELIMINARY",
                "verification_status": "UNVERIFIED",
            },
        }


def _as_2d(array: np.ndarray, *, name: str) -> np.ndarray:
    candidate = np.asarray(array)
    if candidate.ndim == 3 and candidate.shape[2] == 1:
        candidate = candidate[:, :, 0]
    if candidate.ndim != 2:
        raise ValueError(f"{name} must be a single-channel 2D mask.")
    return candidate


def _probabilities(array: np.ndarray) -> np.ndarray:
    probability = _as_2d(array, name="probability mask").astype(np.float32, copy=False)
    if not np.isfinite(probability).all():
        raise ValueError("probability mask contains non-finite values.")
    if probability.max(initial=0.0) > 1.0:
        if probability.max() <= 255.0 and probability.min(initial=0.0) >= 0.0:
            probability = probability / 255.0
        else:
            raise ValueError("probability mask values must be in [0, 1] or an 8-bit [0, 255] encoding.")
    if probability.min(initial=0.0) < 0.0:
        raise ValueError("probability mask values must not be negative.")
    return probability


def _binary_mask(mask: np.ndarray, *, threshold: float) -> np.ndarray:
    candidate = _as_2d(mask, name="mask")
    if np.issubdtype(candidate.dtype, np.floating):
        return _probabilities(candidate) >= threshold
    return candidate.astype(bool, copy=False)


def vectorize_buildings(
    mask: np.ndarray,
    *,
    transform: Affine | None = None,
    crs: CRS | str | object | None = None,
    probability_mask: np.ndarray | None = None,
    config: VectorizationConfig | None = None,
    model_version: str = "unknown",
    source_image: str | None = None,
    source_mask: str | None = None,
) -> VectorizationResult:
    """Trace valid building components without inventing georeferencing or legal status."""
    settings = config or VectorizationConfig()
    binary = _binary_mask(mask, threshold=settings.threshold)
    probability = _probabilities(probability_mask) if probability_mask is not None else None
    if probability is not None:
        if probability.shape != binary.shape:
            raise ValueError("probability mask dimensions must match mask dimensions.")
        binary = probability >= settings.threshold
    coordinate_space: CoordinateSpace = "WORLD" if transform is not None and crs is not None else "PIXEL"
    raster_transform = transform if coordinate_space == "WORLD" else Affine.identity()
    source_crs = crs_to_string(crs) if coordinate_space == "WORLD" else None
    processed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    features: list[BuildingFeature] = []
    for geometry_mapping, value in shapes(binary.astype(np.uint8), mask=binary, connectivity=8, transform=raster_transform):
        if int(value) != 1:
            continue
        raw_geometry = shape(geometry_mapping)
        component_pixels = geometry_mask([geometry_mapping], out_shape=binary.shape, transform=raster_transform, invert=True)
        geometry = clean_polygon(raw_geometry, simplify_tolerance=settings.simplify_tolerance)
        if geometry is None or geometry.is_empty or geometry.area <= 0:
            continue
        area_m2 = area_square_metres(geometry, crs) if coordinate_space == "WORLD" else None
        filter_area = area_m2 if area_m2 is not None else float(geometry.area)
        if filter_area < settings.min_area:
            continue
        confidence = float(probability[component_pixels].mean()) if probability is not None and component_pixels.any() else settings.default_confidence
        features.append(
            BuildingFeature(
                geometry=geometry,
                confidence=confidence,
                area_m2=area_m2,
                area_sqft=area_square_feet(area_m2),
                coordinate_space=coordinate_space,
                crs=source_crs,
                model_version=model_version,
                source_image=source_image,
                source_mask=source_mask,
                processed_at=processed_at,
            )
        )
    return VectorizationResult(
        tuple(features),
        coordinate_space,
        source_crs,
        processed_at,
        {
            "threshold": settings.threshold,
            "min_area": settings.min_area,
            "simplify_tolerance": settings.simplify_tolerance,
            "default_confidence": settings.default_confidence,
        },
    )


def load_mask(path: str | Path) -> np.ndarray:
    """Read a single-channel mask from NumPy or common image files without modifying it."""
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"Mask file does not exist: {source}")
    if source.suffix.lower() == ".npy":
        return _as_2d(np.load(source, allow_pickle=False), name="mask")
    with Image.open(source) as image:
        return _as_2d(np.array(image), name="mask")


def vectorize_from_paths(
    mask_path: str | Path,
    *,
    source_raster: str | Path | None = None,
    probability_mask_path: str | Path | None = None,
    config: VectorizationConfig | None = None,
    model_version: str = "unknown",
    source_image: str | None = None,
    source_mask: str | None = None,
) -> VectorizationResult:
    """Load local inputs and reject any mask/raster size mismatch before vectorization."""
    mask = load_mask(mask_path)
    probability = load_mask(probability_mask_path) if probability_mask_path is not None else None
    transform: Affine | None = None
    crs: CRS | None = None
    if source_raster is not None:
        with rasterio.open(source_raster) as dataset:
            if (dataset.height, dataset.width) != mask.shape:
                raise ValueError(
                    f"Mask dimensions {mask.shape[1]}x{mask.shape[0]} do not match source raster "
                    f"{dataset.width}x{dataset.height}."
                )
            if dataset.crs is not None and dataset.transform != Affine.identity() and dataset.transform.determinant != 0:
                transform = dataset.transform
                crs = CRS.from_user_input(dataset.crs)
    return vectorize_buildings(
        mask,
        transform=transform,
        crs=crs,
        probability_mask=probability,
        config=config,
        model_version=model_version,
        source_image=source_image or (str(source_raster) if source_raster is not None else None),
        source_mask=source_mask or str(mask_path),
    )


def write_geojson(result: VectorizationResult, output_path: str | Path) -> Path:
    """Write a compact FeatureCollection without embedding source rasters or masks."""
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result.to_feature_collection(), indent=2), encoding="utf-8")
    return output

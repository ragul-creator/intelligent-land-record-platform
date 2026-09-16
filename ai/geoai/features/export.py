"""GeoJSON export for C.5 source-backed GIS features."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol

from shapely.geometry import mapping
from shapely.geometry.base import BaseGeometry

from ai.geoai.polygonization.geometry import to_wgs84


class ExportableFeature(Protocol):
    feature_type: str
    coordinate_space: str
    source_crs: str | None
    geometry: BaseGeometry
    status: str
    verification_status: str

    def properties(self, *, exported_crs: str | None = None) -> dict[str, object]: ...


def feature_collection(feature: ExportableFeature) -> dict[str, object]:
    """Export one feature as RFC 7946 GeoJSON, retaining its source CRS in properties."""
    geometry = feature.geometry
    exported_crs = None
    if feature.coordinate_space == "WORLD" and feature.source_crs is not None:
        geometry = to_wgs84(geometry, feature.source_crs)
        exported_crs = "EPSG:4326"
    return {
        "type": "FeatureCollection",
        "features": [{"type": "Feature", "geometry": mapping(geometry), "properties": feature.properties(exported_crs=exported_crs)}],
        "metadata": {
            "feature_type": feature.feature_type,
            "coordinate_space": feature.coordinate_space,
            "source_crs": feature.source_crs,
            "export_crs": exported_crs,
            "status": feature.status,
            "verification_status": feature.verification_status,
        },
    }


def write_feature_geojson(feature: ExportableFeature, output: str | Path) -> Path:
    """Write a compact GeoJSON collection without altering original source input."""
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(feature_collection(feature), indent=2), encoding="utf-8")
    return destination

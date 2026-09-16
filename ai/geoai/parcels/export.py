"""GeoJSON export for C.4 draft parcel results."""

from __future__ import annotations

import json
from pathlib import Path

from shapely.geometry import mapping

from ai.geoai.parcels.models import ParcelResult
from ai.geoai.polygonization.geometry import to_wgs84


def parcel_feature_collection(parcel: ParcelResult) -> dict[str, object]:
    """Return an RFC 7946-style collection while retaining the original source CRS."""
    geometry = parcel.geometry
    exported_crs = None
    if geometry is not None and parcel.coordinate_space == "WORLD" and parcel.source_crs is not None:
        geometry = to_wgs84(geometry, parcel.source_crs)
        exported_crs = "EPSG:4326"
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": mapping(geometry) if geometry is not None else None,
                "properties": parcel.properties(exported_crs=exported_crs),
            }
        ],
        "metadata": {
            "feature_type": "parcel",
            "coordinate_space": parcel.coordinate_space,
            "source_crs": parcel.source_crs,
            "export_crs": exported_crs,
            "status": parcel.status,
            "verification_status": parcel.verification_status,
        },
    }


def write_parcel_geojson(parcel: ParcelResult, output: str | Path) -> Path:
    """Write a compact draft parcel FeatureCollection without changing source input."""
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(parcel_feature_collection(parcel), indent=2), encoding="utf-8")
    return destination

import json
from pathlib import Path

from ai.geoai.runtime.roads import infer_and_vectorize_geotiff

SOURCE = Path(r"C:\Users\ragul\Downloads\2025009FA_MI_Mar_YSMP_SfM_Ortho_5cm.tif")
CHECKPOINT = Path(r"data\models\roads-v2\20260924T113436Z\best-portable.pt")
OUTPUT = Path(r"C:\Users\ragul\Downloads\road-validation-5cm.geojson")

result = infer_and_vectorize_geotiff(
    SOURCE,
    checkpoint=CHECKPOINT,
    device="cuda",
    threshold=0.40,
    min_component_pixels=16,
    simplify_tolerance=0.0,
)

features = []

for feature in result.features:
    features.append({
        "type": "Feature",
        "geometry": feature.geometry.__geo_interface__,
        "properties": {
            "confidence": float(feature.confidence),
            "model_version": feature.model_version,
        },
    })

geojson = {
    "type": "FeatureCollection",
    "name": "BHUMI-AI Road Validation 5cm",
    "crs": {
        "type": "name",
        "properties": {
            "name": result.source_crs,
        },
    },
    "features": features,
}

OUTPUT.write_text(
    json.dumps(geojson, indent=2),
    encoding="utf-8",
)

print("Saved:", OUTPUT)
print("Feature count:", len(features))
print("Source CRS:", result.source_crs)
print("Processing:", result.processing_parameters)

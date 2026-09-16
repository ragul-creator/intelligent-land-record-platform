# GeoAI Building Polygonization (Phase C.3)

Phase C.3 turns C.2 building masks into preliminary building-footprint polygons. It uses Rasterio's 8-connected component tracing and conservative Shapely cleanup. It does not create parcels, legal boundaries, survey identifiers, or approvals.

## Coordinate Handling

Pass `--source-raster` only when it is the same raster or tile that produced the mask. The command requires exactly matching width and height and refuses to stretch a mask over another raster.

- With a valid source-raster affine transform and CRS, output features are `WORLD` coordinates in that source CRS internally. GeoJSON export transforms world geometries to WGS84 (`EPSG:4326`) and retains the original CRS in `properties.source_crs` and collection metadata.
- Without both a transform and CRS, output remains `PIXEL` coordinates. It has no invented CRS and `area_m2` and `area_sqft` are `null`.

Projected geometries use their CRS unit conversion to calculate square metres. Geographic geometries use a PyProj geodesic area calculation, never degree-squared. Square feet use `1 m2 = 10.7639104167 sq ft`.

## Use

```powershell
# C.2 / WHU masks have no georeferencing: output remains PIXEL.
python -m ai.geoai.cli building-vectorize --mask "<MASK.png>" --output data/processed/geoai/buildings/pixel.geojson --model-version building-segmentation-c2-v1

# A mask produced from this exact GeoTIFF/tile: output is WORLD coordinates.
python -m ai.geoai.cli building-vectorize --mask data/processed/geoai/buildings/tile_mask.npy --source-raster data/processed/geoai/tiles/example.tif --probability-mask data/processed/geoai/buildings/tile_probability.npy --output data/processed/geoai/buildings/world.geojson --threshold 0.5 --min-area 10 --simplify-tolerance 0.25 --model-version building-segmentation-c2-v1
```

`--min-area` is in square metres for georeferenced results and pixel-squared units for pixel-space results. `--simplify-tolerance` uses the active coordinate units. Cleanup repairs invalid component geometry and may simplify raster stair-steps, but it does not aggressively rectangularize or merge separate buildings.

Every feature records building class, model confidence, source references, model version, processing time, `AI_PRELIMINARY` status, and `UNVERIFIED` verification status. Probability confidence is the mean probability of component pixels after thresholding. Without a probability mask, confidence is `null` unless a caller supplies it through the Python API.

Generated GeoJSON is written under `data/processed/geoai/` and ignored by Git. Geometry is suitable for later PostGIS loading and topology/review work, but must be checked by an authorized GIS or survey reviewer before publication.

## Debug Overlay

Use the read-only debug overlay to visually inspect C.3 boundaries without altering the source image or GeoJSON:

```powershell
# PIXEL GeoJSON overlays directly on the matching PNG/JPG image.
python -m ai.geoai.cli building-visualize --image "<source.png>" --geojson data/processed/geoai/buildings/pixel.geojson --output data/processed/geoai/buildings/pixel-overlay.png --draw-labels

# WORLD GeoJSON requires the matching GeoTIFF. Its GeoJSON coordinates are transformed back through the raster CRS and inverse affine transform before drawing.
python -m ai.geoai.cli building-visualize --image data/raw/imagery/RGB.byte.tif --source-raster data/raw/imagery/RGB.byte.tif --geojson data/processed/geoai/buildings/world.geojson --output data/processed/geoai/buildings/world-overlay.png
```

The `WORLD` command fails rather than guessing when `--source-raster`, its CRS/affine transform, or matching raster/image dimensions are unavailable.

## Parcel Acquisition (Phase C.4)

C.4 acquires draft parcel candidates only from declared spatial evidence: cadastral GIS, an already-digitized/georeferenced FMB import, GNSS survey corners, future human-drawn geometry, or a supplied visible-boundary candidate. Building footprints are not parcel boundaries. C.4 does not infer invisible legal/property boundaries from imagery.

```powershell
# Cadastral or FMB GeoJSON. The source CRS is required when coordinates are world coordinates.
python -m ai.geoai.cli parcel-create --source-type CADASTRAL_GIS --input "<parcel.geojson>" --source-crs EPSG:32618 --source-reference "cadastral-layer-2026" --output data/processed/geoai/parcels/cadastral-draft.geojson

# Ordered GNSS corners; the input preserves its original point list as provenance.
python -m ai.geoai.cli parcel-create --source-type GNSS_SURVEY --input "<survey-points.json>" --source-crs EPSG:32618 --source-reference "survey-run-42" --output data/processed/geoai/parcels/gnss-draft.geojson

# No visible imagery evidence deliberately creates a null-geometry, survey-required result.
python -m ai.geoai.cli parcel-create --source-type AI_VISIBLE_BOUNDARY --input '{"evidence_type":"NO_VISIBLE_EVIDENCE","coordinate_space":"PIXEL"}' --output data/processed/geoai/parcels/not-determined.geojson
```

World-coordinate output is exported as WGS84 GeoJSON and retains `source_crs`; projected and geographic areas use the same metre-safe and geodesic rules as C.3. `PIXEL`, `LOCAL`, and `UNKNOWN` inputs have null real-world areas. Every result is `DRAFT`/`UNVERIFIED` (or `NOT_DETERMINED` when no boundary can be determined); AI visible evidence also carries `ai_boundary_status: AI_PRELIMINARY` and always requires GIS/survey verification. FMB OCR/georeferencing, imagery boundary inference, legal approval, PostGIS persistence, and Phase D editing remain out of scope.

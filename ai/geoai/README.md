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

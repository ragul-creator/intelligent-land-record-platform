# H.2B.1 Imagery and Building GeoAI

This vertical slice accepts project-scoped GeoTIFF imagery through the existing private MinIO upload flow. The original object is immutable and remains private. A dedicated GeoAI worker validates the GeoTIFF, stores CRS, affine transform, bounds, dimensions, pixel resolution/GSD, nodata, and source metadata on the imagery asset, and creates a derived private PNG preview. The browser receives only a short-lived signed URL for that preview and the actual WGS84 corner coordinates derived from the source affine transform.

## Local use

1. Start the normal Compose services plus `geoai-worker`.
2. Set `GEOAI_CHECKPOINT_HOST_PATH` to the local checkpoint directory (the default mounts `../data/models/buildings` from the Compose file) and set `GEOAI_BUILDING_CHECKPOINT` to its readable in-container path, such as `/models/<run>/best.pt`. Set `GEOAI_DEVICE=auto` to use CUDA when it is available.
3. In a project GIS view, upload a `.tif` or `.tiff`, wait for registration to show `READY`, select it, and choose **Run Building GeoAI**.
4. Buildings appear as `AI_PRELIMINARY` and `UNVERIFIED` footprints with confidence, model version, source imagery reference, timestamp, square metres, and square feet. They are never parcel boundaries.
5. Users with `geo:edit_draft` may choose **Draw New Parcel**, click at least three world-map points, then save a human-drawn `DRAFT`/`UNVERIFIED` parcel through the existing versioned parcel workflow. Review remains the authority for approval.

If no readable `GEOAI_BUILDING_CHECKPOINT` is configured, a requested building job is marked `FAILED` with a safe configuration error. It does not synthesize footprints or pretend model inference ran.

## Limits

This is a practical MVP path for reasonably sized registered GeoTIFFs. It does not tile or stream arbitrarily large orthomosaics during web requests, does not retrain C.2, and does not infer legal identifiers or cadastral ownership. The FastAPI and ordinary Celery worker images deliberately do not contain PyTorch or raster processing dependencies.

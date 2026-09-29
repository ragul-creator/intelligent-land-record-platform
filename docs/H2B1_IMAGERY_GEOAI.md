# H.2B.1 Imagery and Building GeoAI

This vertical slice accepts project-scoped GeoTIFF imagery through the existing private MinIO upload flow. The original object is immutable and remains private. A dedicated GeoAI worker validates the GeoTIFF, stores CRS, affine transform, bounds, dimensions, pixel resolution/GSD, nodata, and source metadata on the imagery asset, and creates a derived private PNG preview. The browser receives only a short-lived signed URL for that preview and the actual WGS84 corner coordinates derived from the source affine transform.

## Local use

1. Start the normal Compose services plus `geoai-worker`.
2. Set `GEOAI_CHECKPOINT_HOST_PATH` to the local checkpoint directory (the default mounts `../data/models/buildings` from the Compose file) and set `GEOAI_BUILDING_CHECKPOINT` to its readable in-container path. The current local pilot uses `/models/mixed_v2/best.pt`.
3. Set `GEOAI_DEVICE=auto` to use CUDA when it is available.
4. In a project GIS view, upload a `.tif` or `.tiff`, wait for registration to show `READY`, select it, and choose **Run Building GeoAI**.
5. Buildings appear as `AI_PRELIMINARY` and `UNVERIFIED` footprints with confidence, model version, source imagery reference, timestamp, square metres, and square feet. They are never parcel boundaries.
6. Users with `geo:edit_draft` may choose **Draw New Parcel**, click at least three world-map points, then save a human-drawn `DRAFT`/`UNVERIFIED` parcel through the existing versioned parcel workflow. Review remains the authority for approval.

If no readable `GEOAI_BUILDING_CHECKPOINT` is configured, a requested building job is marked `FAILED` with a safe configuration error. It does not synthesize footprints or pretend model inference ran.

## Current building model

The current local pilot model is `building-segmentation-c2-mixed-v2`, a DeepLabV3-ResNet50 binary building segmenter fine-tuned from the Karnataka-adapted checkpoint with balanced Karnataka + SpaceNet Vegas sampling.

The fine-tuning source is the local RAMP Karnataka building dataset at:

`C:\datasets\buildings\ramp_karnataka_india\ramp_karnataka_india`

The prepared local split contains:

- train: 4,988 tiles / 39,897 buildings;
- validation: 621 tiles / 5,343 buildings;
- held-out test: 679 tiles / 5,426 buildings.

The selected mixed-domain run is stored locally under:

`data/local/building_mixed_runs/20260927T062633Z/`

and promoted to:

`data/models/buildings/mixed_v2/best.pt`

Its epoch-2 validation at threshold 0.40 records Karnataka IoU 0.7302 / recall 0.8797 and Vegas IoU 0.6802 / recall 0.8330. Production uses a more conservative threshold of 0.60 plus post-processing filters described below.

Real-image regressions through the worker/database path now persist 13 residential candidates and 10 Vegas candidates with model version `building-segmentation-c2-mixed-v2`; the previous Karnataka-only Vegas run produced 531 candidates. The full 7,980 x 10,200 waterfront orthomosaic currently produces 68 candidates.

The original RAMP Karnataka dataset is CC-BY-NC-4.0. This checkpoint is therefore suitable for the current local research/evaluation workflow; commercial deployment requires confirming that the training-data license is appropriate or retraining with suitably licensed data.

## Runtime notes

Building inference preserves train/runtime preprocessing parity: each RGB band is stretched using its 2nd/98th percentiles and then ImageNet-normalized. Production currently uses `GEOAI_BUILDING_THRESHOLD=0.60` and `GEOAI_BUILDING_MIN_AREA_M2=5`. A conservative spectral/shape veto rejects candidates only when blue excess is greater than 0.10 and minimum-rotated-rectangle occupancy is below 0.70; this removes pool/water false positives without rejecting the residential regression roofs.

Checkpoint loading is self-contained: when a full saved checkpoint is loaded, torchvision pretrained weights are not downloaded again. The saved model state provides the backbone weights.

Current polygonization traces the thresholded raster mask directly. This preserves detected area but can leave pixel-stepped edges and can merge touching roofs. Footprint regularization/separation is the next post-processing improvement and must not be used to hide weak segmentation recall.

The GPU GeoAI Celery worker is intentionally configured with concurrency 1 and prefetch 1. Building and road GPU jobs therefore execute serially on the 8 GB RTX 4070 rather than competing for VRAM. Only one worker should consume the `geoai` queue.

## Limits

This remains a practical pilot path. Outputs are model-derived building candidates requiring human review; they do not infer ownership, legal parcel boundaries, statutory identifiers, or cadastral rights. Very large orthomosaics are tiled for bounded inference memory, but production-scale scheduling, model-serving isolation, and target-domain retraining governance still require additional hardening.

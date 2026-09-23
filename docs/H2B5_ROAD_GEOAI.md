# H.2B.5 Road GeoAI

The Road GeoAI MVP uses a separate local binary road-segmentation checkpoint. Registered private GeoTIFF imagery is read by the GeoAI worker, tiled only when needed, inferred once per loaded checkpoint, transformed into preliminary centerlines, and persisted as WGS84 `roads` rows.

`ROAD_VECTORIZE` requires a READY, private-file-backed imagery asset and the existing project-scoped `geoai:process` permission. Missing or invalid checkpoints fail the job without generating roads. Outputs retain the imagery reference, confidence, model version, processing timestamp, and `AI_PRELIMINARY`/`UNVERIFIED` status.

Dataset checks support `<root>/<split>/images` and `<root>/<split>/labels`. Labels can be paired binary rasters or GeoJSON road centerlines, which are rasterized to the imagery grid. No datasets, checkpoints, or training runs are included or triggered.

```bash
python -m ai.geoai.cli road-dataset-check --dataset-root <road-dataset-root> --split train
python -m ai.geoai.cli road-train --dataset-root <road-dataset-root> --epochs 1 --device auto --checkpoint-directory data/models/roads
python -m ai.geoai.cli road-evaluate --dataset-root <road-dataset-root> --split val --checkpoint data/models/roads/<run>/best.pt --device auto
python -m ai.geoai.cli road-infer --checkpoint data/models/roads/<run>/best.pt --source <registered-geotiff> --device auto
```

Training reports pixel IoU, Dice/F1, precision, and recall. It uses the existing CUDA-aware device selection, AMP, pinned DataLoaders, and safe OOM behavior, but this change does not start any training job or claim evaluation results.

Known MVP limitations: the deterministic principal-axis centerline method is appropriate for compact connected road components but may need stronger graph/skeleton post-processing for dense intersections. It never creates road names, classes beyond the neutral `ROAD` default, legal rights-of-way, or official boundaries.

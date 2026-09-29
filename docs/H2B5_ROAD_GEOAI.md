# H.2B.5 Road GeoAI

The Road GeoAI path now uses a dedicated Docker-managed SAM-Road service rather than the earlier local binary-segmentation MVP. Registered private GeoTIFF imagery is read only by the GeoAI worker, sent over the private Compose network to `http://samroad:8765`, vectorized, and persisted as WGS84 `roads` rows.

`ROAD_VECTORIZE` requires a READY, private-file-backed imagery asset plus the existing project-scoped `geoai:process` permission. Outputs remain `AI_PRELIMINARY` and `UNVERIFIED`; they are evidence candidates for human review, never legal rights-of-way or cadastral boundaries.

## SAM-Road v7 production configuration

The local pilot configuration is:

- model version: `sam-road-spacenet-vitb-centerline-v7`;
- target inference GSD: 0.8 m/pixel;
- minimum canvas: 400 px;
- high-bit imagery normalization: fixed `0..2047 -> 0..255` RGB scaling;
- road-mask threshold: 0.31;
- centerline simplification: 0.5 inference pixels;
- topology support radius: 3 inference pixels;
- no mask/topology rejection in production;
- review flag: graph support < 0.89 while mask confidence >= 0.88.

v5 briefly used that disagreement condition as a hard rejection rule. Testing on a narrow unpaved residential lane showed that this could suppress a legitimate road, so v6 restored recall-first behavior. v7 keeps that recall-first policy and adds a targeted geometry refinement: only candidates with `topology_disagreement_flag: true` are nudged toward the midpoint of their own detected road mask, with a hard maximum shift of 0.5 inference pixels. Normal roads and junctions are left untouched. Width-only filtering remains disabled because validation showed that it removed too many valid roads.
## Runtime architecture

Compose now supervises the `samroad` GPU service. The GeoAI worker uses the internal service name rather than `host.docker.internal`, avoiding dependence on the lifetime of a separate WSL shell process.

The road worker retries transient `URLError`/connection failures with backoff up to three times. A successful inference with zero features is still a successful job: stale unverified AI candidates for that imagery are removed, `feature_count: 0` is persisted, and the frontend reports that no road candidates were detected.

The frontend renders SAM-Road v2-v7 candidates in bright red for inspection and filters unverified imagery candidates to the currently active imagery asset. Road-job output metadata also records per-road confidence, graph-support diagnostics, and the `topology_disagreement_flag` so reviewers can distinguish a model disagreement from a missing road.

## Local model files

The Docker runtime is mounted from the ignored local directory:

`data/local/samroad_runtime/`

It contains the SAM-Road runtime code plus the local checkpoints required by the service. Model artifacts remain outside Git.

## Health and post-sleep recovery

Run this from the repository root:

`powershell -ExecutionPolicy Bypass -File infrastructure\check-local-health.ps1`

The check verifies Docker, the Windows GPU, Docker CUDA access, Compose services, frontend/backend HTTP health, SAM-Road v7/CUDA health, and the GeoAI-worker -> SAM-Road network path.

On this laptop, long Windows sleep can occasionally break Docker Desktop's WSL/NVIDIA adapter bridge. If the health check reports that Docker CUDA has no adapters, restart Docker Desktop, then run:

`docker compose --env-file .env -f infrastructure\docker-compose.yml up -d`
## Validation record

The current v7 regression record is stored locally at:

`D:\datasets\spacenet\sn3_khartoum_subset100\v7_regression_summary.json`

The held-out 15-image Khartoum centerline regression records mean precision 0.8105, mean GT-road coverage 0.5306, mean harmonic score 0.6389, median harmonic score 0.7234, and 30.3 features per image. These are effectively unchanged from v6. The narrow unpaved lane is retained as one road candidate with `topology_disagreement_flag: true` and receives a targeted ~0.48 px recentering nudge; the Vegas regression remains nine road features and receives zero recenter shift because its candidates are not flagged.

Dataset checks, local training/evaluation utilities, and the earlier road model remain available under `ai/geoai`, but the current Web-GIS Road GeoAI path uses SAM-Road v7.

## Safety and scope

Road outputs do not create road names, statutory road classes, ownership conclusions, legal access rights, parcel boundaries, or government-approved records. Human review remains required for any operational use.

# Intelligent Land Record Platform

Phase A repository foundation for the combined SIH 12 urban cadastral mapping and SIH 18 land-record digitization platform. The approved architecture is documented in [`docs/team/00_MASTER_BLUEPRINT_v2.0.pdf`](docs/team/00_MASTER_BLUEPRINT_v2.0.pdf).

## Prerequisites

- Node.js 20 or later
- Python 3.12 or later
- Docker Desktop with Docker Compose v2 (for containerized local development)

## Local development

1. Copy `.env.example` to `.env` and replace the placeholder credentials with local development values. Do not commit `.env`.
2. Start the backend:

   ```powershell
   cd backend
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -e ".[dev]"
   uvicorn app.main:app --reload --port 8000
   ```

   The health endpoint is available at `http://localhost:8000/health`.

3. Start the frontend in another terminal:

   ```powershell
   cd frontend
   npm install
   npm run dev
   ```

   The development UI is available at `http://localhost:5173`.

## Docker Compose

After creating `.env`, start the local service foundation from the repository root:

```powershell
docker compose --env-file .env -f infrastructure/docker-compose.yml up --build
```

This starts the frontend, FastAPI backend, Celery worker, PostgreSQL/PostGIS, Redis, and private MinIO service. Apply the database schema explicitly with Alembic as described below. Phase B provides backend authentication, RBAC, project isolation, private storage, and job persistence; OCR, GeoAI, GIS, and frontend application features remain outside this phase.

### H.2B.1 imagery and GeoAI

The Compose stack also includes a dedicated `geoai-worker`; FastAPI and the regular worker remain free of PyTorch and raster processing dependencies. Upload a project GeoTIFF from the GIS view, wait for registration, and run building processing only after provisioning a local C.2 checkpoint at `GEOAI_BUILDING_CHECKPOINT` in the GeoAI worker. Leave the variable blank to fail a building job safely instead of fabricating output. See [H.2B1 imagery and GeoAI](docs/H2B1_IMAGERY_GEOAI.md) for the private-preview, provenance, manual draft, and review workflow.

Stop the stack with:

```powershell
docker compose --env-file .env -f infrastructure/docker-compose.yml down
```

## Database Foundation

Phase B.1 uses PostgreSQL 16 with PostGIS and a private Compose-network database. Start the stack before applying migrations:

```powershell
docker compose --env-file .env -f infrastructure/docker-compose.yml up --build -d
docker compose --env-file .env -f infrastructure/docker-compose.yml exec backend alembic upgrade head
```

Create a future migration after updating SQLAlchemy models:

```powershell
docker compose --env-file .env -f infrastructure/docker-compose.yml exec backend alembic revision --autogenerate -m "describe_change"
docker compose --env-file .env -f infrastructure/docker-compose.yml exec backend alembic upgrade head
```

Validate migration state, PostgreSQL connectivity, and PostGIS:

```powershell
docker compose --env-file .env -f infrastructure/docker-compose.yml exec backend alembic check
docker compose --env-file .env -f infrastructure/docker-compose.yml exec backend python -c "from sqlalchemy import text; from app.core.database import engine; print(engine.connect().execute(text('SELECT PostGIS_Version()')).scalar_one())"
```

## Tests

```powershell
cd backend
pytest

# Database integration tests after `alembic upgrade head`
docker compose --env-file .env -f infrastructure/docker-compose.yml exec -e RUN_DATABASE_TESTS=1 backend pytest

cd ..\frontend
npm test
```

## Private Storage And Jobs

Phase B.2 uses the existing MinIO service as private S3-compatible object storage. The backend and worker communicate with it over the Compose network at `http://minio:9000`; do not use `localhost` from a container. Development host endpoints are `http://localhost:9001` for the MinIO API and `http://localhost:9002` for the MinIO console.

The backend creates a `PENDING_UPLOAD` file row and a server-generated immutable object key, then returns a 15-minute signed PUT URL from `POST /api/v1/files/presign`. The client uploads directly to MinIO with the returned headers and calls `POST /api/v1/files/complete`. The backend verifies object metadata before changing the file to `UPLOADED`, creates an idempotent `FILE_REGISTERED` processing job, and queues the minimal worker task. `GET /api/v1/files/{file_id}/download` returns a short-lived signed GET URL only for completed private files. Permanent public URLs and client-provided storage keys are never used.

Apply the B.2 migration and start the worker locally with Docker Compose:

```powershell
docker compose --env-file .env -f infrastructure/docker-compose.yml up --build -d
docker compose --env-file .env -f infrastructure/docker-compose.yml exec backend alembic upgrade head
docker compose --env-file .env -f infrastructure/docker-compose.yml logs worker
```

For a non-Docker worker, configure `REDIS_URL`, database variables, and S3 variables from `.env`, then run:

```powershell
cd backend
celery -A app.workers.celery_app.celery_app worker --loglevel=info
```

Run the storage, Redis, MinIO, and database integration tests after the stack and migrations are ready:

```powershell
docker compose --env-file .env -f infrastructure/docker-compose.yml exec -e RUN_DATABASE_TESTS=1 backend pytest
```

## Authentication And Authorization

Phase B.3 implements Argon2id password hashing, short-lived bearer access tokens, and server-side revocable refresh sessions. Access tokens are valid for 15 minutes by default. Refresh tokens are valid for seven days by default, are stored only as SHA-256 digests, and rotate on every successful refresh. Replaying a rotated token or using a logged-out token is rejected.

The backend enforces the approved application role-to-permission matrix through `role_permissions`. A permission alone never grants access to every project: each project-owned operation also requires a `project_members` record. This includes `ADMIN`, which has all approved permissions but no implicit cross-project membership bypass. The protected B.2 file endpoints require bearer authentication, the relevant permission, and membership in the file's project.

Configure the following local values in `.env`; do not commit them:

```dotenv
AUTH_JWT_SECRET=<long-random-local-secret>
AUTH_ACCESS_TOKEN_LIFETIME_SECONDS=900
AUTH_REFRESH_TOKEN_LIFETIME_SECONDS=604800
```

After migrations are applied, temporarily set `BOOTSTRAP_ADMIN_EMAIL`, `BOOTSTRAP_ADMIN_PASSWORD`, and optionally `BOOTSTRAP_ADMIN_FULL_NAME` or `BOOTSTRAP_ADMIN_LOGIN_ID` in the ignored `.env` file. Then create the initial local administrator once. When no login ID is supplied, the command creates a unique `ADM-TN-<sequence>` ID and prints that ID only; it never prints the password or tokens:

```powershell
docker compose --env-file .env -f infrastructure/docker-compose.yml run --rm backend python -m app.cli.bootstrap_admin
```

Clear the bootstrap password from `.env` after the command succeeds.

Authentication APIs are `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`, `POST /api/v1/auth/logout`, and `GET /api/v1/users/me`. Login accepts `{ "identifier": "ADM-TN-000001", "password": "..." }` or an email address as `identifier`; the legacy `email` request field remains accepted for compatibility. `/users/me` returns the immutable human-facing `login_id` alongside the canonical UUID `id`. Login failures deliberately use a generic response. Audit records retain security-action identifiers and safe metadata only; they never include passwords, raw tokens, signed URLs, or storage credentials.

## Phase B Project APIs

Phase B.4 adds project-scoped backend contracts for later workflow and dashboard work. Every project-owned endpoint requires both the relevant global permission and a `project_members` record; `ADMIN` has no automatic cross-project bypass. Project listings contain only the caller's memberships, and inaccessible project, file, and job IDs intentionally use a not-found response to reduce resource enumeration.

- `POST /api/v1/projects`, `GET /api/v1/projects`, `GET/PATCH /api/v1/projects/{project_id}` use `project:create`, `project:read`, and `project:update` as applicable.
- `GET/POST /api/v1/projects/{project_id}/members` and `PATCH/DELETE /api/v1/projects/{project_id}/members/{user_id}` require `project:member_manage`. A membership role is only a project-context label: the target must already hold that application role globally, so membership cannot grant or escalate RBAC permissions. Inactive users cannot be added, and the owner membership is protected.
- `GET /api/v1/projects/{project_id}/summary`, `/workflow`, `/jobs`, and `/audit` provide persisted file/job/member/audit information only. Workflow reports only observed `UPLOADED`, `QUEUED`, `PROCESSING`, or `FAILED` evidence; it does not fabricate OCR or GeoAI stages. Audit reads require `audit:read`.
- `GET /api/v1/processing-jobs/{job_id}` and project job lists expose safe metadata only. Cancellation and retry APIs are intentionally not exposed until a future workflow implementation can guarantee safe side effects.

List endpoints use bounded `limit` and `offset` query parameters (`limit` defaults to 50 and cannot exceed 100). Application errors use `{ "error": { "code": "...", "message": "..." } }`; request validation remains HTTP 422 with the same envelope but omits rejected values to avoid echoing sensitive inputs.

File completion is idempotent: repeating `POST /api/v1/files/complete` for an uploaded file returns its original registration job. Upload category is persisted so completion rechecks the same required permission used for presigning. Buckets remain private and all client storage access uses short-lived signed URLs. Job transitions are constrained to `QUEUED -> PROCESSING -> COMPLETED|FAILED`; worker failures persist only a generic error signal.

`GET /health` is a liveness endpoint. `GET /ready` verifies PostgreSQL/PostGIS, Redis, and the private MinIO bucket. Celery worker liveness is not part of synchronous readiness because broker-level worker inspection would make readiness brittle; worker status remains observable through Compose and persisted job state.

The next planned phase is **Phase C: GeoAI**. Current Phase B deliberately does not implement OCR/HTR, GeoAI, Web-GIS, document extraction, statutory approval, or frontend login/dashboard UI.

## Document AI Integration (Phase F.4)

Phase F.4 connects the existing F.1 OCR, F.2 extraction, and F.3 validation libraries to the private storage, PostgreSQL, Celery, audit, and E.2 review foundations. A project member uploads a PDF, PNG, JPG/JPEG, or TIFF/TIF through `POST /api/v1/projects/{project_id}/documents`. The immutable original is stored privately in MinIO through the existing storage adapter; `GET .../{document_id}/source-url` requires `document:read` and returns only a short-lived signed URL.

`POST .../{document_id}/process` queues an idempotent `DOCUMENT_AI_PROCESS` job, and `POST .../{document_id}/reprocess` creates a new append-only OCR version. The worker applies `F.1 -> F.2 -> F.3`, preserving OCR page/token evidence, extracted candidates, validation reports, confidence summaries, provenance, and timestamps. The document progresses through `UPLOADED`, `QUEUED`, `PROCESSING`, `EXTRACTED`, `VALIDATING`, then `REVIEW_REQUIRED` or `VALIDATED`; it never auto-publishes a land record. Failures are safely reported as `FAILED` without deleting previous versions.

Read APIs are `GET .../{document_id}`, `/ocr`, `/fields`, and `/validation`. A `REVIEW_REQUIRED` result creates exactly one E.2 `DOCUMENT` task for that persisted validation version, with the document UUID as its target. Field corrections use `POST .../fields/{field_id}/corrections`, require `field:correct` and a reason, and create a versioned correction record without changing OCR or extracted evidence. The correction ID is a valid E.2 `CORRECT` reference.

The React demo route is `/projects/{project_id}/documents`. It keeps preliminary OCR, original extracted values, confidence, validation issues, and later corrections visibly separate, and links review-required documents to the existing Review Workspace. Backend permissions remain authoritative.

The backend/worker Docker image installs Tesseract plus the English and Tamil Debian language packages for the documented demo. OCR languages remain configurable through the process request; other language packs must be installed and evaluated separately. Handwritten-document OCR remains preliminary and requires human review. No government verification is configured, so F.3 reports that check as `NOT_PERFORMED`.

## GeoAI Persistence (Phase C.7)

Phase C.7 connects the existing GeoAI parcel acquisition and topology libraries to PostGIS and Celery. `POST /api/v1/projects/{project_id}/geoai/jobs` creates a project-scoped asynchronous job; `PARCEL_IMPORT` is the complete supported path. The worker invokes the existing C.4 `create_parcel()` code, stores declared world geometry as EPSG:4326, retains `source_crs` and input provenance, creates a parcel plus immutable geometry version 1, and records safe audit events. `GET /api/v1/projects/{project_id}/geoai/jobs/{job_id}` and `/cancel` expose project-scoped job state.

Parcel reads are available at `GET /api/v1/projects/{project_id}/parcels`, `/{parcel_id}`, and `/{parcel_id}/versions`. `POST /api/v1/projects/{project_id}/parcels/{parcel_id}/versions` requires `geo:edit_draft`; it invokes C.6 validation and appends a new human version under a database lock. Earlier versions are never overwritten. Significant changes or topology overlaps return `REVIEW_REQUIRED`; invalid geometry is rejected. These remain preliminary draft geometries, not statutory or legal approval. Phase D will consume this version API.

Run the stack, migrate, and execute database-backed tests:

```powershell
docker compose --env-file .env -f infrastructure/docker-compose.yml up --build -d
docker compose --env-file .env -f infrastructure/docker-compose.yml exec backend alembic upgrade head
docker compose --env-file .env -f infrastructure/docker-compose.yml exec -e RUN_DATABASE_TESTS=1 backend pytest -v
```

The backend/worker image uses the repository root only to import the existing lightweight C.4/C.6 geometry modules; its root `.dockerignore` excludes local data, generated artifacts, model checkpoints, environments, and Git state. Building/road/land-use job adapters are registered types only in this phase; model execution, frontend editing, approval workflows, and government integration are intentionally not implemented.

## Read-only Web-GIS (Phase D.1)

The React viewer is available at `/projects/{project_id}/gis` to an authenticated project member with `geo:read`. It reads the existing parcel-version API and the project-scoped `buildings`, `roads`, `land-use`, and unresolved `topology-errors` endpoints. The viewer deliberately keeps building footprints distinct from parcel boundaries, represents missing geometry as absent rather than invented, and labels every displayed cadastral feature as draft/preliminary and unverified where applicable.

MapLibre GL JS renders the layer stack locally in the browser. The optional OpenStreetMap raster basemap is a visual reference only; no external imagery is required for the project data layers. Parcel selection is read-only and shows current version/provenance/area metadata plus a version-history list. Red dashed parcel outlines indicate an unresolved topology record associated with that parcel; they do not imply a geometry for the topology error itself. D.1 does not include vertex editing, version creation, review actions, approval, or legal/statutory determinations.

Start the frontend and open a project route after obtaining an existing application session token through the backend authentication API:

```powershell
cd frontend
npm run dev
# http://localhost:5173/projects/<project-uuid>/gis
```

Run the frontend checks with `npm test` and `npm run build`. Backend project-scope integration checks run after the Compose stack is migrated with `RUN_DATABASE_TESTS=1` as shown above.

## Draft Parcel Editing (Phase D.2)

The same `/projects/{project_id}/gis` route now supports a Polygon-only draft edit session for a project member whose backend profile includes `geo:edit_draft`. It uses `/api/v1/users/me` only to decide whether to expose the editor; the backend remains authoritative for permission and project-membership enforcement. View-only users do not receive edit controls.

An edit session clones the current GeoJSON geometry in memory, renders the original dashed outline plus the edited boundary and vertex handles, and supports vertex drag, edge-click insertion, selected-vertex deletion, undo, redo, reset, and cancel. MultiPolygon and `NOT_DETERMINED`/missing geometries remain read-only with an explicit message. The browser preview uses a spherical geodesic calculation for the WGS84 API geometry; it is only a responsive indication. C.6/PostGIS calculates and validates the authoritative result when saving.

`Save Draft` requires a change reason and sends the existing immutable-version endpoint with `expected_current_version`. A stale draft gets `409 PARCEL_VERSION_CONFLICT` and remains open for correction/refresh. A successful save appends a new human geometry version, refreshes parcel/history/topology reads, and displays either the new version or `Saved — Review Required` with C.6 issue reasons. D.2 does not approve, publish, certify, or make a parcel legally authoritative.

## GeoAI Raster Ingestion (Phase C.1)

Phase C.1 provides a local, reusable GeoTIFF ingestion foundation only. It validates readable GeoTIFF inputs, CRS/EPSG when resolvable, affine transform, bounds, pixel resolution, bands, dtype, and NoData before extracting typed JSON metadata. It performs no segmentation, building detection, inference, polygonization, or area calculation.

Install the dedicated GeoAI development dependencies from the repository root. Rasterio publishes GDAL-compatible Linux wheels, and the isolated [GeoAI Dockerfile](ai/geoai/Dockerfile) can be built with `docker build -f ai/geoai/Dockerfile .` without increasing the existing backend image.

```powershell
python -m pip install -r ai/geoai/requirements.txt
python -m ai.geoai.cli inspect data/raw/imagery/RGB.byte.tif
python -m ai.geoai.cli tile data/raw/imagery/RGB.byte.tif --tile-size 512
```

Tiles are written by default to `data/processed/geoai/tiles/<source_stem>/` with deterministic names such as `RGB.byte_r0000_c0001.tif`. Each tile is written as a GeoTIFF using its actual Rasterio pixel window transform and bounds, preserving CRS, band data, dtype, and NoData. Edge tiles are smaller rather than padded. Generated imagery, tiles, and model artifacts are ignored by Git.

## Building Segmentation MVP (Phase C.2)

Phase C.2 adds pixel-space building-footprint segmentation for the WHU PNG dataset. It uses a swappable torchvision `DeepLabV3-ResNet50` adapter with a binary output head, selected for mature PyTorch support and a practical path to explicitly requested ImageNet backbone weights. By default it creates no network request for pretrained weights; `--pretrained-backbone` is the explicit opt-in and torchvision may download/cache ImageNet weights when they are not already local. Normal inference always uses a supplied local checkpoint.

WHU must be supplied outside this repository with the following layout. Pass its root at the CLI; no machine-specific path is stored in code.

```text
WHU/
  train/Image/*.png  train/Mask/*.png
  val/Image/*.png    val/Mask/*.png
  test/Image/*.png   test/Mask/*.png
```

Install the dedicated GeoAI requirements, validate a split, then run a small smoke training job before any full run:

```powershell
python -m pip install -r ai/geoai/requirements.txt
python -m ai.geoai.cli dataset-check --dataset-root "<WHU_ROOT>" --split train
python -m ai.geoai.cli building-train --dataset-root "<WHU_ROOT>" --epochs 1 --batch-size 2 --limit 16 --device auto
python -m ai.geoai.cli building-infer --checkpoint "data/models/buildings/<run_id>/best.pt" --input "<WHU_ROOT>\val\Image" --output-dir data/processed/geoai/buildings/demo --device auto
python -m ai.geoai.cli building-evaluate --dataset-root "<WHU_ROOT>" --split val --checkpoint "data/models/buildings/<run_id>/best.pt" --limit 32 --device auto
```

`--device auto` selects CUDA whenever `torch.cuda.is_available()` succeeds; `--device cuda` fails clearly rather than silently falling back. Startup logs report device, GPU name, CUDA version, VRAM, and CPU logical cores. CUDA training uses AMP, GradScaler, cuDNN benchmarking, pinned-memory DataLoaders, non-blocking transfers, and `zero_grad(set_to_none=True)`. `--num-workers` is auto-sized conservatively from logical CPU cores, while explicit values remain supported. If CUDA runs out of memory, reduce `--batch-size`; C.2 reports the failure and never silently falls back to CPU.

Checkpoints are saved under `data/models/buildings/<run_id>/`; predictions are saved under `data/processed/geoai/buildings/<run_id>/` as deterministic probability arrays, binary PNG masks, and prediction metadata. Reported metrics are IoU/Jaccard, Dice/F1, precision, and recall. Per-prediction confidence is model-only: mean probability over predicted building pixels, predicted-building fraction, and threshold. It is not legal, parcel, or cadastral confidence.

WHU PNG imagery is not georeferenced. C.2 does not invent CRS, affine transforms, areas, parcels, or polygons; C.3 will connect model masks to the C.1 GeoTIFF/world-coordinate pipeline. Native image dimensions are retained unless `--image-size N` is supplied; that option resizes image and mask together (bilinear and nearest-neighbor respectively). For a later full GPU run, start conservatively on the available hardware, for example: `python -m ai.geoai.cli building-train --dataset-root "<WHU_ROOT>" --epochs 30 --batch-size 4 --image-size 256 --device cuda --num-workers 4`. Do not start this full run until smoke validation is reviewed.

## Repository layout

- `frontend/` - React + TypeScript client; it communicates only with backend APIs.
- `backend/` - FastAPI public integration boundary.
- `ai/document_ai/` - preliminary PDF/image preprocessing and Tamil/English OCR; see [`ai/document_ai/README.md`](ai/document_ai/README.md).
- `ai/geoai/`, `ai/validation/` - internal package boundaries for later asynchronous services.
- `infrastructure/` - local container configuration.
- `data/` - local-only samples and annotations (not source-of-truth production storage).
- `docs/` - approved architecture and future API/deployment documentation.

## Phase G.3 End-to-End Integrated Demo Flow

Phase G.3 connects the completed document, GIS, review, dashboard, and G.1 record-to-parcel workflows into one navigable demo path without adding new statutory claims or bypassing existing RBAC.

The project dashboard at `/projects/{project_id}` now presents the guided vertical slice: open the SIH18 Documents workspace, process and validate a land-record document, inspect SIH12 cadastral evidence in Web-GIS, resolve any human-review task, and then inspect or confirm the persisted Record ↔ Parcel workflow association. Document-to-parcel candidate generation still uses the G.1 deterministic matcher and persisted evidence only; imagery alone never establishes an official identifier.

The Documents workspace displays persisted Record ↔ Parcel candidates for the selected validated record, including method, confidence, review state, and direct navigation to the associated parcel in Web-GIS. Authorized users with `validation:run` may generate candidates, while `validation:resolve` controls confirmation/rejection. Rejecting an association requires a reason. The GIS parcel panel performs the reverse lookup and links back to the source document, preserving a complete demo trail in both directions.

Deep links are supported with `?documentId=<uuid>` on the Documents route and `?parcelId=<uuid>` on the Web-GIS route. These are navigation aids only; backend project membership and permission checks remain authoritative.

The integrated demo does not auto-publish land records, certify ownership, or treat an AI-derived parcel as a legal boundary. Confirmed Record ↔ Parcel links remain auditable workflow associations. Final statutory verification and live government-system integration require external departmental processes/adapters.

## Phase H.1 Regression And Security Hardening

Phase H.1 adds regression coverage and targeted hardening across authentication, project isolation, RBAC, human review, Document AI workflow states, async retry/failure semantics, audit redaction, and the browser API boundary. The backend now accepts browser requests only from explicitly configured `CORS_ALLOWED_ORIGINS` (default local demo origin `http://localhost:5173`); wildcard CORS is rejected.

Retryable Celery failures are persisted back to `QUEUED` so the configured retry can actually run, while final failures retain only a generic error signal. Inactive reviewers cannot receive new assignments, and Document AI reprocessing requires prior persisted OCR evidence. See [`docs/H1_REGRESSION_SECURITY.md`](docs/H1_REGRESSION_SECURITY.md) for the verification scope and known MVP limits.

## Phase H.2 Tamil Nadu Demo

Phase H.2 adds a browser sign-in/project launcher and an idempotent **synthetic** Tamil Nadu demo dataset. The seed includes Tamil + English OCR evidence, structured fields, a human-review case, draft cadastral parcels, building/pathway/land-use GIS layers, and a confirmed workflow association between one validated record and one parcel. It does not contain official survey or ownership data.

After migrations are applied, seed the demo with a local password:

```powershell
$env:DEMO_SEED_PASSWORD = "replace-with-a-local-demo-password"
docker compose -f infrastructure\docker-compose.yml --env-file .env run --rm --build `
  -e DEMO_SEED_PASSWORD=$env:DEMO_SEED_PASSWORD `
  backend `
  python -m app.cli.seed_demo
```

The command prints the generated role login IDs and project UUID. Re-running the seed refreshes the synthetic demo-account passwords to the supplied `DEMO_SEED_PASSWORD` without duplicating the demo project. Open `http://localhost:5173/`, sign in, and launch **Tamil Nadu Integrated Land Records Demo** from the project chooser. See [`docs/H2_DEMO_GUIDE.md`](docs/H2_DEMO_GUIDE.md) for the presentation sequence and synthetic-data boundaries.

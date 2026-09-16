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

This starts the frontend, FastAPI backend, Celery worker, PostgreSQL/PostGIS, Redis, and private MinIO service. Apply the database schema explicitly with Alembic as described below. Authentication and RBAC enforcement, OCR, GeoAI, GIS, and frontend application features remain outside Phase B.2.

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

## Repository layout

- `frontend/` - React + TypeScript client; it communicates only with backend APIs.
- `backend/` - FastAPI public integration boundary.
- `ai/document_ai/`, `ai/geoai/`, `ai/validation/` - internal package boundaries for later asynchronous services.
- `infrastructure/` - local container configuration.
- `data/` - local-only samples and annotations (not source-of-truth production storage).
- `docs/` - approved architecture and future API/deployment documentation.

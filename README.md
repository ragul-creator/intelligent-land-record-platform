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

This starts the frontend, FastAPI backend, PostgreSQL/PostGIS, Redis, and private MinIO service. It does not initialize a production database schema, authentication, RBAC, async workers, OCR, or GeoAI models; those remain outside Phase A.

Stop the stack with:

```powershell
docker compose --env-file .env -f infrastructure/docker-compose.yml down
```

## Tests

```powershell
cd backend
pytest

cd ..\frontend
npm test
```

## Repository layout

- `frontend/` - React + TypeScript client; it communicates only with backend APIs.
- `backend/` - FastAPI public integration boundary.
- `ai/document_ai/`, `ai/geoai/`, `ai/validation/` - internal package boundaries for later asynchronous services.
- `infrastructure/` - local container configuration.
- `data/` - local-only samples and annotations (not source-of-truth production storage).
- `docs/` - approved architecture and future API/deployment documentation.

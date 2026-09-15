# Intelligent Land Record Platform — Agent Instructions

## Project Goal

Build a combined SIH 12 + SIH 18 Intelligent Land Record Platform.

SIH 12:
AI-enabled urban cadastral mapping from:
- drone imagery
- ORI / orthomosaics
- DSM / DTM
- existing GIS/cadastral layers
- GT data
- GNSS/CORS data

SIH 18:
AI-powered land-record digitization and validation from:
- scanned PDFs
- handwritten registers
- maps
- legacy land-record documents

---

## Source of Truth

The approved project documentation is located in:

docs/team/

Always read:

docs/team/00_MASTER_BLUEPRINT_v2.0.pdf

Then read the relevant person-specific PDF before working on that subsystem.

The documentation is the baseline architecture.

Do not silently replace:
- architecture
- APIs
- database entities
- workflow states
- RBAC permissions
- subsystem responsibilities

If something is unclear, report the ambiguity instead of inventing a major change.

---

## Team Responsibility Map

Person 1:
Project Lead, System Architecture and Integration.

Person 2:
Document AI, OCR and HTR.

Person 3:
Validation and Data Quality.

Person 4:
GeoAI, Urban Cadastral Mapping and GIS Processing.

Person 5:
Backend, Database, Security and Deployment.

Person 6:
Frontend, Web-GIS, Dashboards and Human Verification.

---

## Approved Technology Stack

Frontend:
- React
- TypeScript
- Web-GIS

Backend:
- FastAPI

Database:
- PostgreSQL
- PostGIS

Storage:
- MinIO / S3-compatible private object storage

Async processing:
- Redis
- Celery

AI:
- Document AI
- OCR / HTR
- NLP / structured extraction
- GeoAI segmentation / object detection
- Validation and data-quality services

GIS:
- PostGIS
- GeoJSON
- GIS-ready vector data

---

## Repository Structure

Use the existing repository structure.

Expected top-level areas include:

frontend/
backend/
ai/
infrastructure/
data/
docs/

Inside ai/:

ai/document_ai/
ai/geoai/
ai/validation/

Do not create a second conflicting project structure.

Add subdirectories only when needed for implementation.

---

## Critical Engineering Rules

1. Frontend must never access PostgreSQL/PostGIS directly.

2. Authentication and authorization must be enforced by the backend.

3. Use both RBAC and project-scoped authorization.

4. Original uploaded files must remain immutable.

5. Private files must not use permanent public URLs.

6. Heavy OCR, GeoAI, GIS and validation processing must run asynchronously.

7. Async jobs should support retries and idempotent execution where appropriate.

8. Every significant AI output must include:
   - confidence
   - provenance/source reference
   - model version
   - processing timestamp

9. Never invent legal or official land identifiers from imagery.

Examples:
- survey number
- khasra number
- khata number
- official parcel ID

10. AI-derived parcel boundaries are preliminary until authorized review.

11. Preserve:
   - CRS
   - EPSG
   - georeferencing
   - affine/world transforms

12. Do not calculate parcel/building area directly from latitude/longitude degrees.

13. Use an appropriate projected CRS or validated geodesic calculation.

14. Building and parcel area must support:
   - square metres
   - square feet

15. Validation issues must link back to supporting evidence.

16. Corrections and generated outputs must be auditable and versioned.

17. Government/LRMS/DILRMP integrations must use adapters/interfaces unless a real authorized API is available.

18. Never commit:
   - passwords
   - secrets
   - access keys
   - API tokens
   - production credentials

---

## Application Roles

The main application roles are:

- ADMIN
- OFFICER
- REVIEWER
- SURVEYOR
- VIEWER

Do not confuse these application RBAC roles with the six engineering team roles.

Authorization must be enforced server-side.

---

## Workflow Principles

Document workflow baseline:

UPLOADED
-> QUEUED
-> PROCESSING
-> EXTRACTED
-> VALIDATING
-> REVIEW_REQUIRED
-> VALIDATED
-> PUBLISHED

Failures should support retry from the appropriate failed stage.

GeoAI workflow baseline:

Input imagery/GIS data
-> input validation
-> tiling
-> AI inference
-> prediction merge
-> polygonization
-> geometry repair
-> area calculation
-> topology validation
-> cadastral comparison
-> preliminary features
-> human GIS/survey review
-> published GIS layer

---

## API Rules

The approved API definitions in the project PDFs are the integration baseline.

Do not casually rename or redesign major endpoints.

Prefer:

/api/v1/...

Use typed request/response schemas.

Keep frontend/backend contracts consistent.

Use OpenAPI wherever practical.

---

## Development Rules

Do not build the entire platform in one task.

Work phase-by-phase.

For every task:

1. Read AGENTS.md.
2. Inspect the current repository.
3. Read the relevant PDF specification.
4. State a short implementation plan.
5. Modify only the requested scope.
6. Run relevant tests.
7. Fix implementation-caused failures.
8. Verify affected services start where practical.
9. Report files created/modified.
10. Report tests/commands executed.
11. Report assumptions or unresolved issues.
12. Stop after completing the requested scope.

Do not automatically continue to the next phase.

---

## Development Phases

Phase A:
Repository foundation and contracts.

Phase B:
Backend, PostgreSQL/PostGIS, storage, authentication and RBAC.

Phase C:
SIH 12 GeoAI MVP.

Phase D:
Web-GIS, cadastral visualization and area measurement.

Phase E:
Validation and human-review workflows.

Phase F:
SIH 18 Document AI / OCR / HTR / extraction.

Phase G:
End-to-end SIH 12 + SIH 18 integration.

Phase H:
Testing, evaluation, deployment preparation and demo polish.

---

## MVP Priority

First SIH 12 demo:

GeoTIFF / ORI
-> GeoAI
-> building detection
-> building polygons
-> PostGIS
-> Web-GIS
-> click building
-> display area in m² and sq ft

Then add:

existing parcel layer
-> AI geometry comparison
-> validation
-> human review
-> approved GIS layer

Then add SIH 18:

scanned land record
-> OCR / HTR
-> structured field extraction
-> confidence
-> provenance
-> validation
-> human correction
-> structured land record

---

## Quality Rule

Prefer one complete, tested vertical slice over a large amount of incomplete generated code.
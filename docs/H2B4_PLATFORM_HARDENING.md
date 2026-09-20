# H.2B.4 platform hardening

H.2B.4 completes the platform usability and operational-hardening scope for the hackathon/MVP/pilot-ready prototype. It does not claim production government readiness. Existing RBAC, audit, immutable evidence, and human-review rules remain authoritative.

## Completed scope

H.2B.4 covers all nine planned items:

1. audit UI;
2. admin/project-management UI;
3. project creation;
4. refresh-token/session hardening;
5. asynchronous job progress and recovery;
6. stronger project dashboard;
7. Viewer draft-visibility rules;
8. project search;
9. evidence-preserving exports.

No new database migration is required for this phase. The implementation builds on the existing project, membership, audit, authentication, processing-job, document, GIS, and review persistence models.

## Audit UI

The existing project audit endpoint now supports optional exact filters for:

- action;
- target type;
- actor ID.

The frontend Audit workspace exposes project-scoped events and sanitized metadata. Access requires the existing `audit:read` permission and project membership.

Audit history remains append-only. The UI does not provide edit or delete controls.

## Administration and project creation

Authorized users can create a project directly from the signed-in landing page using the existing `project:create` permission.

Project creation:

- creates an isolated ACTIVE project;
- records the authenticated user as owner;
- creates an owner project membership using an application role the user already holds;
- records `project.created` in the audit log.

The Project management workspace uses the existing project and membership APIs for:

- project name, description, and ACTIVE/ARCHIVED state;
- membership inspection;
- member addition;
- project-role changes;
- member removal.

Owner membership remains protected from role change/removal. User discovery is exposed through a permission-gated `GET /api/v1/users` directory requiring `user:manage`. Adding a project member cannot grant a role the target user does not already hold globally.

## Session hardening

The backend already used revocable, rotating refresh sessions. H.2B.4 completes the browser-side workflow and binds newly issued access tokens to the same persisted auth session, so refresh rotation or logout invalidates the associated access token immediately rather than waiting only for JWT expiry.

The frontend now:

- stores access/refresh tokens plus calculated expiry timestamps in session storage;
- refreshes shortly before access-token expiry;
- performs a single refresh flight when several requests need renewal;
- retries one API request after a 401 when refresh succeeds;
- stores the newly rotated refresh token;
- clears rejected/expired sessions and emits a session-expiry event;
- clears local session state even when logout delivery itself fails.

A transient network error while refreshing does not immediately erase stored credentials; the subsequent API response still determines whether the session is usable.

## Asynchronous jobs and recovery

The Processing jobs workspace lists persisted project jobs with:

- job type;
- status;
- progress percentage;
- retry count;
- failure signal;
- recovery hint.

The page polls while QUEUED/PROCESSING work exists.

A new `POST /api/v1/processing-jobs/{job_id}/retry` endpoint safely requeues supported FAILED jobs when the caller has the workflow-specific permission:

- `DOCUMENT_AI_PROCESS` → `document:reprocess`;
- `DOCUMENT_REVALIDATE` → `field:correct`;
- `PARCEL_IMPORT` / `BUILDING_VECTORIZE` → `geoai:process`;
- `IMAGERY_REGISTER` → `imagery:upload`.

Recovery preserves the existing workflow identity, increments the persisted retry count, clears the safe failure signal, resets progress, records `processing_job.manual_retry_queued`, and dispatches the original workflow against persisted source/request context. Only FAILED supported jobs can be manually retried.

## Stronger dashboard

The combined project dashboard now adds:

- open validation-issue count;
- active job count;
- failed job count;
- retryable failed job count;
- validation issues in the attention summary;
- explicit draft-data visibility policy;
- navigation to Search, Jobs, Exports, Audit, and Project admin based on permissions.

Counts continue to be derived from persisted project data; no synthetic completion percentage is presented.

## Viewer draft visibility

Viewer access is explicitly read-only.

A Viewer with the existing read permissions may inspect draft or unverified document/GIS evidence so that project context remains visible, but:

- draft/preliminary and verification labels remain visible;
- document upload/processing/correction actions remain permission-gated;
- GIS editing, imagery upload, and GeoAI processing remain permission-gated;
- review and administration mutations remain unavailable;
- job retry remains unavailable.

This policy is surfaced in the dashboard, Document AI workspace, and Web-GIS workspace. Read visibility does not convert draft evidence into an approved record or legal boundary.

## Project search

`GET /api/v1/projects/{project_id}/search?q=...` requires project access plus `project:read`.

The current MVP search covers:

- source document filenames;
- parcel external identifiers;
- persisted extracted field values and latest human-corrected values when the caller also has `field:read`.

Results include the workflow status and a `preliminary` flag. Viewer accounts therefore do not gain field-level access merely by using project search. Search is evidence discovery only and does not infer identity, ownership, or cadastral boundaries.

## Exports

`GET /api/v1/projects/{project_id}/exports` returns the export manifest for users with `export:read`.

Two evidence-preserving exports are provided:

### Land-record evidence CSV

`GET /api/v1/projects/{project_id}/exports/records.csv`

The CSV includes document/workflow status and latest validation status. Selected record fields and the latest human correction are included only when the caller already has `field:read`, or when the document is VALIDATED and the caller has `record:read`. This prevents `export:read` from becoming a shortcut around field-level RBAC. The CSV carries an evidence note stating that exported values are not statutory ownership proof.

### Parcel GeoJSON

`GET /api/v1/projects/{project_id}/exports/parcels.geojson`

The GeoJSON contains only persisted parcel geometry. Each feature retains:

- external identifier;
- status;
- verification status;
- source and source reference;
- geometry version;
- stored areas;
- a preliminary flag;
- `legal_boundary_asserted: false`.

The export explicitly states that draft/unverified geometry is not statutory boundary certification.

GeoPackage and external GIS/LRMS/DILRMP adapters remain H.2B.5 scope.

## New/extended API surface

- `GET /api/v1/users`
- `GET /api/v1/projects/{project_id}/audit?action=&target_type=&actor_id=`
- `POST /api/v1/processing-jobs/{job_id}/retry`
- `GET /api/v1/projects/{project_id}/search?q=`
- `GET /api/v1/projects/{project_id}/exports`
- `GET /api/v1/projects/{project_id}/exports/records.csv`
- `GET /api/v1/projects/{project_id}/exports/parcels.geojson`

Existing project creation, project update, membership, authentication, dashboard, document, and GIS endpoints are reused rather than duplicated.

## Focused validation commands

Backend H.2B.4 integration coverage:

```powershell
docker compose -f infrastructure\docker-compose.yml --env-file .env run --rm --build `
  -e RUN_DATABASE_TESTS=1 `
  backend `
  python -m pytest tests/test_h2b4_platform_hardening_integration.py -q
```

Frontend H.2B.4 workspace and session coverage:

```powershell
Set-Location frontend
npm test -- --run src/tests/H2B4Platform.test.tsx src/tests/session.test.ts
npm run build
Set-Location ..
```

Before the H.2B.4 pull request, also run the complete backend and frontend regression suites plus `git diff --check` and verify the working tree is clean.

# Phase H.1 — Regression, Security, RBAC, Workflow, and Failure-Path Verification

Phase H.1 hardens the existing hackathon/pilot prototype. It does not claim government production certification, statutory approval, or external penetration-test coverage.

## Verified boundaries

- Authentication keeps Argon2id password hashes, typed access/refresh JWTs, refresh-session rotation/revocation, inactive-user denial, and generic login failures.
- Project authorization remains permission **plus** project membership. ADMIN has no implicit cross-project bypass.
- The canonical role/permission matrix is frozen by a regression test for ADMIN, OFFICER, REVIEWER, SURVEYOR, and VIEWER.
- Representative live APIs verify role separation for dashboard reads, project updates, review reads, audit reads, and project-member management.
- Private storage keys remain server generated. Signed URLs and credential-like audit metadata are not persisted through the audit service.
- Record ↔ Parcel links remain project scoped and non-statutory workflow associations; existing G.1 tests continue to cover cross-project isolation and viewer write denial.
- GIS draft edits retain optimistic version-conflict protection; document originals/evidence remain append-only through existing F.4 tests.

## H.1 fixes

1. **Browser API boundary:** added explicit configurable CORS for the frontend origin. Wildcard origins are rejected; credentials are not enabled. This allows Authorization-header preflight requests from the demo frontend without opening the API to arbitrary browser origins.
2. **Review assignment:** inactive users can no longer receive review assignment/escalation even if a stale project membership remains.
3. **Document workflow:** `/reprocess` can no longer be used before a prior OCR result exists.
4. **Async retries:** Celery retryable failures now return persisted jobs from PROCESSING to QUEUED before autoretry. The final attempt becomes FAILED with a generic error only. Deterministic GeoAI service errors remain terminal and are not retried.
5. **Registration worker:** PROCESSING is committed before terminal completion so a later transient failure can be represented and retried safely.

## Failure semantics

The generic processing-job API never exposes raw worker exception text. Persisted `error_json` remains a safe generic signal. Retry count must increase monotonically. Terminal jobs cannot be completed/cancelled/retried through internal state helpers after an invalid transition.

Document AI retry attempts preserve the immutable original source and roll back uncommitted OCR/extraction/validation rows before requeueing. GeoAI retry attempts roll back uncommitted parcel output before requeueing.

## Local H.1 verification

Run non-database tests and static compilation:

```powershell
$env:PYTHONPATH = "$PWD;$PWD\backend"
python -m pytest backend\tests -q
python -m compileall -q backend\app ai
git diff --check
```

Run the full live PostgreSQL/PostGIS/Redis/MinIO-backed backend suite:

```powershell
docker compose -f infrastructure\docker-compose.yml --env-file .env run --rm `
  -e RUN_DATABASE_TESTS=1 `
  -e PYTHONPATH=/workspace:/workspace/backend `
  -v "${PWD}:/workspace" `
  -w /workspace/backend `
  backend `
  python -m pytest tests -q
```

Frontend regression remains part of H.1 because CORS and end-to-end navigation are browser-facing:

```powershell
cd frontend
npm test
npm run build
```

## Known scope limits

This phase is application regression hardening, not a third-party security audit. Live LRMS/DILRMP/government APIs remain adapters/demo representations unless real departmental endpoints and credentials are supplied. External infrastructure hardening, TLS/reverse-proxy policy, secret-manager deployment, backup/restore operations, and formal load/penetration testing belong to deployment/production hardening rather than this two-day MVP.

# H.2B.3 validation gaps

This phase adds conservative cross-record validation on top of the existing validated-document and record-to-parcel workflows. It remains an MVP/pilot-ready prototype: validation flags require human review and do not establish legal ownership, statutory approval, or cadastral boundaries.

## Scope

H.2B.3 closes three gaps:

1. duplicate-record checks;
2. area-mismatch validation between validated document evidence and a confirmed parcel association;
3. a validation-issues UI integrated into the existing human-review workspace.

No new database table or migration is required. Validation issues are persisted as ordinary `review_tasks` with `target_type=VALIDATION_ISSUE`, which preserves the existing assignment, reviewer-action, audit-history, and RBAC workflow.

## Duplicate-record check

The check considers the latest `VALID` validation result for each `VALIDATED` document in a project. It compares only persisted extracted identifier fields already recognized by the record-to-parcel workflow, including survey/cadastral/parcel identifiers.

Comparison normalization is deterministic and comparison-only. Original extracted evidence is never changed. Repeated matching candidates inside the same document do not count as multiple records.

When the same normalized identifier appears in two or more validated documents, one project-scoped issue is created for that identifier. The issue records:

- all implicated document IDs and validation-result IDs;
- source field/page references;
- the normalized identifier and original display value;
- an explicit interpretation that identifier reuse is a review flag, not automatic proof of a legal duplicate.

## Area-mismatch check

Area comparison runs only when a record-to-parcel link is already `CONFIRMED`, the validated document contains a supported `plot_area`, and the parcel's current geometry version has a stored area.

Supported document units are square metres and square feet. The default relative tolerance is 5%:

```
abs(document_area_m2 - parcel_area_m2) / max(document_area_m2, parcel_area_m2)
```

A difference above 5% creates an `AREA_MISMATCH` review issue. Differences of at least 20% are marked `HIGH`; smaller mismatches above the threshold are `MEDIUM`.

The check compares stored areas only. It does not reinterpret geographic degrees as area, change parcel geometry, or infer a legal parcel boundary.

## Idempotence and review workflow

Each issue has a stable validation issue key. Re-running validation:

- creates a task when the issue has not been seen before;
- refreshes evidence/metadata for an existing open issue;
- does not duplicate or automatically reopen an already resolved issue.

The run itself is recorded in the audit log as `validation.h2b3_checks_run`.

Reviewer actions continue through the existing review API. `APPROVE`, `REJECT`, comments, assignment, escalation, correction references, and reprocessing therefore retain the same audit semantics as other review tasks.

## API

- `POST /api/v1/projects/{project_id}/validation/run`
  - requires project access plus `validation:run`;
  - runs duplicate-record and area-mismatch checks;
  - returns created/refreshed counts and the current open validation issues.

- `GET /api/v1/projects/{project_id}/validation/issues`
  - requires project access plus `review:read`;
  - supports status, severity, issue type, assignee, limit, and offset filters;
  - issue types are `DUPLICATE_RECORD` and `AREA_MISMATCH`.

## UI

The Review workspace now includes a **Validation issues** tab alongside Document review and GIS review.

Reviewers can:

- filter by status, severity, issue type, and assignment;
- run validation checks when they have `validation:run`;
- inspect source references and structured issue metadata;
- navigate to document evidence and, for area mismatches, parcel GIS evidence;
- use the existing auditable reviewer actions to resolve or annotate an issue.

## Validation commands

Pure backend policy coverage:

```powershell
python -m pytest backend/tests/test_h2b3_validation.py -q
```

Database/API integration coverage in the existing Docker backend environment:

```powershell
docker compose -f infrastructure\docker-compose.yml --env-file .env run --rm `
  -e RUN_DATABASE_TESTS=1 `
  backend `
  python -m pytest tests/test_h2b3_validation_integration.py -q
```

Frontend focused coverage:

```powershell
Set-Location frontend
npm test -- --run src/tests/ReviewPage.test.tsx
npm run build
Set-Location ..
```

Before PR creation, also run the normal broader backend/frontend regression suites and `git diff --check`.

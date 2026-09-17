# Phase D.3 — Web-GIS Integration and Demo Hardening

Phase D.3 hardens the existing `/projects/{project_id}/gis` viewer/editor without changing the legal or workflow meaning of parcel data.

## User-facing behavior

- GIS data refreshes automatically every 30 seconds and when the browser regains focus while no parcel edit session is active.
- A manual **Refresh GIS data** action is available for demonstrations and recovery from stale data.
- Automatic/manual refresh is paused while a parcel is being edited so an in-progress draft is not disturbed.
- Layer visibility choices are persisted per project in browser local storage.
- If a background refresh fails after data was already loaded, the last successful dataset remains visible with a warning instead of replacing the map with a fatal error page.
- If `/users/me` cannot verify editing permissions, the map remains usable in read-only mode and shows an explicit warning.
- Unresolved topology findings linked to a selected parcel are listed with severity, code, message, and affected area when available. No missing topology geometry is invented.
- Successful version saves show the created version and whether C.6 returned `REVIEW_REQUIRED`; the banner can be dismissed.

## Safety and authority

The backend remains authoritative for `geo:read`, `geo:edit_draft`, project membership, optimistic version concurrency, C.6 validation, and immutable parcel version creation. Browser-persisted layer preferences are presentation state only.

Parcel geometry remains draft/preliminary until authorized verification. Building footprints remain separate from parcel boundaries. Phase D.3 does not add approval, publication, legal certification, or government-system integration.

## Validation

Run from `frontend/`:

```powershell
npm test
npm run build
```

If a separate TypeScript or lint command is configured, run it as well. From the repository root also run:

```powershell
git diff --check
```

No database migration is introduced by D.3. Existing backend/PostGIS tests should remain unchanged.

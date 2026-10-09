# Integrated features and review fixes

This branch combines the current `deploy/vercel-sih` application with the
GeoPackage interchange branch and the previously local demo integration adapters.
It preserves the existing XLSX export, land-use inference, and parcel delineation.

## Features

- GeoPackage inspection, private uploads, background imports with run tracking,
  and project GeoPackage exports. Export downloads appear in the existing manifest.
- Offline synchronization API with project scope, operation replay protection,
  version conflict responses, and a change feed. An IndexedDB queue, service worker,
  and offline frontend interaction are still needed for a complete field client.
- Generic GIS, LRMS, and DILRMP **demo** adapters with capability, preview, and
  acknowledgement endpoints. They make no departmental network calls and never
  certify a land record. Unknown adapter names return request validation errors.

The Solidity branch is intentionally deferred: its registration function currently
allows any caller to claim a globally identified record. Authorized registrars,
identifier scope, chain governance, and correction/revocation rules need design.
The local Fabric branch also needs a real network adapter and deployment validation
before it can be described as a production ledger.

## Fixes

- One Alembic head merges the previously diverged histories and restores the union
  of GeoPackage, parcel delineation, and land-use job constraints.
- Affine transforms use the supported `*` operator, fixing four GeoAI test failures.
- Frontend requests use `VITE_API_BASE_URL`; Docker passes it during the static
  build. Changing this setting requires rebuilding the frontend image.
- GIS layers and parcel history follow all pages; incomplete page responses fail
  visibly instead of producing a silently truncated map.
- Original object PUTs require signed `If-None-Match: *`, including server uploads.
  Upload completion hashes actual stored bytes in 1 MiB chunks and checks them
  against the registration; client-controlled metadata is insufficient evidence.
- Project deletion removes GeoPackage import runs in FK-safe order and no longer
  references the absent `immutable_ledger_outbox` table.
- Refresh token rotation locks its session row so simultaneous requests cannot
  both redeem the same token.
- Corrected extraction values use the existing field normalizers before validation,
  retaining area units and identifier structure without altering the original row.
- SAMRoad builds without an implicitly prebuilt local image, is optional, and is
  reachable only inside the Compose network. GPU reservations are an override.
- GitHub Actions runs the non-live backend/document/validation suites, migration SQL
  generation, and the frontend tests and build.

## Configuration

Set `VITE_API_BASE_URL` to the browser-reachable backend origin before building.
The default is `http://localhost:8000`. For Vercel, supply the variable in the
frontend build environment. For Compose, `.env` is passed as the build argument.
Storage CORS rules must allow `If-None-Match` in addition to the existing upload
headers. The S3-compatible provider must support conditional PutObject; an
unsupported condition must fail rather than fall back to overwriting originals.
Completion reads the object once for integrity verification, so account for its
latency when sizing upload request timeouts for large rasters.

For SAMRoad, first provision the existing local runtime and checkpoints described
in its setup documentation, set `SAMROAD_SERVICE_URL=http://samroad:8765`, and use:

```sh
docker compose --env-file .env -f infrastructure/docker-compose.yml --profile samroad up --build
```

On a host configured for NVIDIA containers, add the GPU override:

```sh
docker compose --env-file .env -f infrastructure/docker-compose.yml -f infrastructure/docker-compose.gpu.yml --profile samroad up --build
```

Apply `alembic upgrade head` before running the combined application. This advances
either previous branch head to the shared merge revision.

## Remaining work before production rollout

Live PostGIS/Redis/MinIO integration tests, concurrent refresh validation, browser
upload replay, and container builds must run on a working isolated test stack.
The current local Docker engine was unavailable during the review. Do not treat
generated SQL as proof that it was executed against a database.

The broader review still identifies durable job dispatch/recovery, retaining old
GeoAI result sets, document approval and correction-reference semantics, correction
revalidation during active jobs, Viewer draft visibility policy, and validation
scan scaling. Those require separate workflow/schema changes. The frontend bundle
also remains large and should be split by route.

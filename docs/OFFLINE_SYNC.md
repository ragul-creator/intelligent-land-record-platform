# Offline-First Synchronization & Conflict Resolution Backend

## Overview

The BHUMI-AI / Intelligent Land Record Platform provides a robust, offline-first synchronization protocol and conflict resolution engine. This allows mobile surveyors, field reviewers, and offline operators to capture geometry modifications, human document corrections, review decisions, and record-parcel link resolutions while disconnected from the network, and seamlessly synchronize them once connectivity is restored.

---

## Architecture & Principles

1. **Explicit Offline Mutation Scope (Phase 1)**:
   Only four discrete business operation types are supported for offline batch submission:
   - `PARCEL_VERSION_CREATE`
   - `FIELD_CORRECTION_CREATE`
   - `REVIEW_TASK_UPDATE`
   - `RECORD_LINK_RESOLVE`

2. **Online-Only Operations**:
   The following actions are strictly online-only and must **never** be queued offline:
   - Document and raster imagery uploads
   - MinIO presigned URL requests
   - Imagery georeferencing/registration runs
   - Building and Road GeoAI model execution
   - Project membership and administration
   - Batch validation-run executions
   - Background job retries
   - Bulk exports and hard deletes

3. **Transaction Isolation & Partial Batch Success**:
   - Each operation in a batch is executed inside an isolated database savepoint (`session.begin_nested()`).
   - If one operation encounters a version conflict or validation failure, it does **not** roll back unrelated successful operations in the batch.

4. **Global Idempotency**:
   - Every operation submitted by a client must supply a unique client-generated UUID `operation_id`.
   - Replaying or submitting an identical `operation_id` is safe and idempotent: the backend returns the stored outcome with status `DUPLICATE` without duplicating the underlying domain mutation.

5. **No "Last Write Wins"**:
   - Stale parcel edits and illegal review state transitions return `CONFLICT` with structured version/state metadata. The user or frontend resolves conflicts explicitly.

6. **Monotonic Change Feed**:
   - Every successful domain mutation generates an event in `sync_changes` with an autoincrementing BIGINT cursor (`id`), enabling clients to catch up without omissions or duplicates.

---

## Authentication & Authorization

All sync endpoints require a valid Bearer token and project membership:
- `Authorization: Bearer <access_token>`
- The caller must be an active project member with the required permission for each operation type:

| Operation Type | Required Permission | Description |
| :--- | :--- | :--- |
| `PARCEL_VERSION_CREATE` | `geo:edit_draft` | Append draft geometry version to a parcel |
| `FIELD_CORRECTION_CREATE` | `field:correct` | Append human field correction to extracted field |
| `REVIEW_TASK_UPDATE` | `review:act` | Action or reassign a review task |
| `RECORD_LINK_RESOLVE` | `validation:resolve` | Confirm or reject a record-parcel suggestion |

---

## API Endpoints

### 1. Submit Sync Batch

- **URL**: `POST /api/v1/projects/{project_id}/sync/batch`
- **Rate/Batch Limit**: Maximum 100 operations per request (`SYNC_BATCH_TOO_LARGE` if exceeded).
- **Status Codes**:
  - `200 OK`: Batch envelope validated and processed (contains individual operation outcomes).
  - `401 UNAUTHORIZED`: Missing or invalid authentication token.
  - `403 FORBIDDEN`: User is not a member of the project or lacks project access.
  - `422 UNPROCESSABLE CONTENT`: Invalid request envelope structure or batch size > 100.

#### Request Schema

```json
{
  "client_id": "5f60ec68-89d7-4be4-85f4-2fb83229ab88",
  "operations": [
    {
      "operation_id": "64c360ad-0931-43ee-9f4a-b5f64bd57916",
      "operation_type": "PARCEL_VERSION_CREATE",
      "entity_id": "8b9a896d-3174-4b57-9d7e-9087c53f1a23",
      "base_version": 7,
      "client_created_at": "2026-09-24T12:10:00Z",
      "payload": {
        "geometry": {
          "type": "Polygon",
          "coordinates": [
            [
              [77.0, 28.0],
              [77.01, 28.0],
              [77.01, 28.01],
              [77.0, 28.01],
              [77.0, 28.0]
            ]
          ]
        },
        "source_crs": "EPSG:4326",
        "change_reason": "Field GPS boundary update"
      }
    }
  ]
}
```

#### Operation Payloads

##### A. `PARCEL_VERSION_CREATE`
- `entity_id`: Parcel UUID
- `base_version`: (Mandatory integer) Must equal the parcel's `current_geometry_version` at time of draft creation.
- `payload`:
  - `geometry`: GeoJSON Polygon dict
  - `source_crs`: e.g. `"EPSG:4326"` or projected CRS
  - `change_reason`: (Optional string) Reason for change

##### B. `FIELD_CORRECTION_CREATE`
- `entity_id`: `DocumentExtractedField` UUID
- `payload`:
  - `corrected_value`: (Mandatory string) Human-corrected field text
  - `reason`: (Mandatory string) Rationale for human correction

##### C. `REVIEW_TASK_UPDATE`
- `entity_id`: `ReviewTask` UUID
- `payload`:
  - `action`: `"APPROVE" | "CORRECT" | "REJECT" | "REPROCESS" | "COMMENT" | "ESCALATE"`
  - `assignee_user_id`: (Optional UUID) Target user for assignment or escalation
  - `reason`: (Optional/Mandatory depending on action) Text rationale
  - `correction_reference`: (Required for `CORRECT`) Reference to versioned correction
  - `reprocess_job_id`: (Required for `REPROCESS`) New processing job UUID

##### D. `RECORD_LINK_RESOLVE`
- `entity_id`: `RecordParcelLink` UUID
- `payload`:
  - `action`: `"CONFIRM" | "REJECT"`
  - `reason`: (Required when rejecting) Rationale string

#### Response Schema

```json
{
  "project_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "server_cursor": "1842",
  "results": [
    {
      "operation_id": "64c360ad-0931-43ee-9f4a-b5f64bd57916",
      "status": "APPLIED",
      "entity_type": "PARCEL",
      "entity_id": "8b9a896d-3174-4b57-9d7e-9087c53f1a23",
      "server_version": 8,
      "result": {
        "parcel_version": 8,
        "status": "VALID",
        "area_m2": 1000.0,
        "area_sqft": 10763.9
      },
      "conflict": null,
      "error": null
    }
  ]
}
```

---

### 2. Change Feed API

- **URL**: `GET /api/v1/projects/{project_id}/sync/changes?cursor={cursor}&limit={limit}`
- **Parameters**:
  - `cursor`: (Optional string) Monotonic sequence ID. If omitted or `0`, begins from the start.
  - `limit`: (Optional integer, default: 50, max: 100) Maximum records to return.
- **Response**:

```json
{
  "items": [
    {
      "cursor": "1842",
      "entity_type": "PARCEL",
      "entity_id": "8b9a896d-3174-4b57-9d7e-9087c53f1a23",
      "change_type": "UPDATED",
      "server_version": 8,
      "changed_at": "2026-09-24T12:11:04Z"
    }
  ],
  "next_cursor": "1842",
  "has_more": false
}
```

---

### 3. Operation Recovery API

- **URL**: `GET /api/v1/projects/{project_id}/sync/operations/{operation_id}`
- **Description**: Allows a client to query the terminal state of a previously submitted `operation_id` if a network disconnect occurred before receiving the batch response.
- **Response**:

```json
{
  "id": "64c360ad-0931-43ee-9f4a-b5f64bd57916",
  "project_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "user_id": "9a01...-....",
  "client_id": "5f60ec68-89d7-4be4-85f4-2fb83229ab88",
  "operation_type": "PARCEL_VERSION_CREATE",
  "entity_id": "8b9a896d-3174-4b57-9d7e-9087c53f1a23",
  "base_version": 7,
  "status": "APPLIED",
  "result": {
    "parcel_version": 8,
    "status": "VALID",
    "area_m2": 1000.0,
    "area_sqft": 10763.9
  },
  "conflict": null,
  "error_code": null,
  "error_message": null,
  "client_created_at": "2026-09-24T12:10:00Z",
  "applied_at": "2026-09-24T12:11:04Z",
  "created_at": "2026-09-24T12:11:04Z"
}
```

---

## Operation Statuses & Conflict Resolution

Each operation in `results` returns one of four statuses:

1. `APPLIED`:
   - The mutation was successfully validated and committed into the PostgreSQL database.
   - Corresponding change event and audit logs were recorded.

2. `DUPLICATE`:
   - An identical `operation_id` was already processed and persisted earlier.
   - No duplicate domain changes are performed. The original result payload is returned.

3. `CONFLICT`:
   - The operation could not be applied due to a concurrent version mismatch or illegal state transition.
   - **Parcel Version Conflict**: When `base_version != parcel.current_geometry_version`, returns:
     ```json
     {
       "conflict_type": "VERSION_MISMATCH",
       "expected_version": 7,
       "server_version": 8,
       "message": "This parcel has newer geometry version 8. Base version was 7."
     }
     ```
   - **Review State Conflict**: When a review task is already resolved or in an incompatible state, returns:
     ```json
     {
       "conflict_type": "STATE_CONFLICT",
       "server_state": "RESOLVED",
       "message": "Resolved review tasks cannot be changed."
     }
     ```

4. `REJECTED`:
   - The operation is permanently invalid (e.g., entity not found, invalid geometry, missing permission, unsupported operation).
   - Includes standard error details:
     ```json
     {
       "code": "SYNC_ENTITY_NOT_FOUND",
       "message": "The target parcel was not found in this project."
     }
     ```

---

## Standard Error Codes

| Error Code | HTTP Status | Description |
| :--- | :--- | :--- |
| `SYNC_BATCH_TOO_LARGE` | 422 | Batch contains more than 100 operations |
| `SYNC_OPERATION_UNSUPPORTED` | 200 (in item) | Operation type not in Phase 1 scope |
| `SYNC_OPERATION_INVALID` | 200 (in item) | Missing required fields, invalid geometry, or malformed payload |
| `SYNC_ENTITY_NOT_FOUND` | 200 (in item) | Target entity ID does not exist in the requested project |
| `SYNC_PERMISSION_DENIED` | 200 (in item) | Caller lacks required RBAC permission for this operation type |
| `SYNC_VERSION_CONFLICT` | 200 (in item) | Base version does not match current server version |
| `SYNC_STATE_CONFLICT` | 200 (in item) | Review task or record link is in an incompatible state |
| `SYNC_OPERATION_NOT_FOUND` | 404 | Operation recovery ID not found in project |

---

## Frontend Integration & Retry Guidelines

1. **Client Storage**:
   - Store offline drafts locally with a generated UUIDv4 `operation_id` and the current known `base_version`.
   - On reconnect, submit all pending operations in a single `POST /sync/batch` request (splitting into chunks of <= 100 if necessary).

2. **Handling Batch Responses**:
   - `status === "APPLIED"`: Mark local operation as synchronized and update local entity version.
   - `status === "DUPLICATE"`: Mark local operation as synchronized.
   - `status === "CONFLICT"`: Prompt the surveyor/reviewer with the server version/geometry for side-by-side comparison and resolution.
   - `status === "REJECTED"`: Log validation error and notify the operator; do not automatically retry without human modification.

3. **Change Catch-Up**:
   - Maintain the last received `server_cursor`.
   - Poll `GET /sync/changes?cursor={last_cursor}` to retrieve incremental changes across the project.
   - Stop when `has_more === false`.

4. **Security Reminders**:
   - Never serialize authentication tokens, passwords, or signed URLs in offline storage or sync payloads.

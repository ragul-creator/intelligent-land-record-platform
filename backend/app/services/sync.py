"""Offline-first synchronization service and conflict resolution engine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit.service import record_audit, sanitize_audit_metadata
from app.core.auth import user_permissions
from app.core.errors import ApiError, forbidden, not_found
from app.models import (
    Document,
    DocumentExtractedField,
    Parcel,
    RecordParcelLink,
    ReviewTask,
    SyncChange,
    SyncOperation,
    User,
)
from app.schemas.sync import (
    SyncBatchRequest,
    SyncBatchResponse,
    SyncChangeItem,
    SyncChangesResponse,
    SyncConflictDetail,
    SyncErrorDetail,
    SyncOperationDetailResponse,
    SyncOperationItemRequest,
    SyncOperationResult,
)
from app.services.documents import create_correction, queue_correction_revalidation
from app.services.geoai import (
    GeoAIServiceError,
    ParcelVersionConflict,
    create_human_parcel_version,
)
from app.services.record_parcel_links import (
    RecordParcelLinkError,
    resolve_link,
)
from app.services.review import ReviewWorkflowError, update_review_task

MAX_SYNC_BATCH_SIZE = 100

SUPPORTED_OPERATIONS = frozenset(
    {
        "PARCEL_VERSION_CREATE",
        "FIELD_CORRECTION_CREATE",
        "REVIEW_TASK_UPDATE",
        "RECORD_LINK_RESOLVE",
    }
)

OPERATION_PERMISSIONS = {
    "PARCEL_VERSION_CREATE": "geo:edit_draft",
    "FIELD_CORRECTION_CREATE": "field:correct",
    "REVIEW_TASK_UPDATE": "review:act",
    "RECORD_LINK_RESOLVE": "validation:resolve",
}

OPERATION_ENTITY_TYPES = {
    "PARCEL_VERSION_CREATE": "PARCEL",
    "FIELD_CORRECTION_CREATE": "DOCUMENT_FIELD",
    "REVIEW_TASK_UPDATE": "REVIEW_TASK",
    "RECORD_LINK_RESOLVE": "RECORD_PARCEL_LINK",
}


def _record_sync_change(
    session: Session,
    *,
    project_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    change_type: str,
    server_version: int | None = None,
) -> SyncChange:
    change = SyncChange(
        project_id=project_id,
        entity_type=entity_type,
        entity_id=entity_id,
        change_type=change_type,
        server_version=server_version,
    )
    session.add(change)
    return change


def _persist_sync_operation(
    session: Session,
    *,
    operation_id: uuid.UUID,
    project_id: uuid.UUID,
    user_id: uuid.UUID | None,
    client_id: str | None,
    operation_type: str,
    entity_id: uuid.UUID,
    base_version: int | None,
    payload: dict[str, Any],
    status: str,
    result: dict[str, Any] | None = None,
    conflict: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    client_created_at: datetime | None = None,
) -> SyncOperation:
    existing = session.get(SyncOperation, operation_id)
    if existing is not None:
        return existing

    record = SyncOperation(
        id=operation_id,
        project_id=project_id,
        user_id=user_id,
        client_id=client_id,
        operation_type=operation_type,
        entity_id=entity_id,
        base_version=base_version,
        payload_json=sanitize_audit_metadata(payload),
        status=status,
        result_json=result,
        conflict_json=conflict,
        error_code=error_code,
        error_message=error_message,
        client_created_at=client_created_at,
        applied_at=datetime.now(UTC) if status == "APPLIED" else None,
    )
    session.add(record)
    return record


def _handle_parcel_version_create(
    session: Session,
    *,
    project_id: uuid.UUID,
    user: User,
    op: SyncOperationItemRequest,
    client_id: str | None,
) -> SyncOperationResult:
    if op.base_version is None:
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="REJECTED",
            error_code="SYNC_OPERATION_INVALID",
            error_message="base_version is mandatory for PARCEL_VERSION_CREATE.",
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="REJECTED",
            entity_type="PARCEL",
            entity_id=op.entity_id,
            error=SyncErrorDetail(
                code="SYNC_OPERATION_INVALID",
                message="base_version is mandatory for PARCEL_VERSION_CREATE.",
            ),
        )

    geometry = op.payload.get("geometry")
    source_crs = op.payload.get("source_crs", "EPSG:4326")
    change_reason = op.payload.get("change_reason")

    if not isinstance(geometry, dict) or not source_crs:
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="REJECTED",
            error_code="SYNC_OPERATION_INVALID",
            error_message="Payload must include valid geometry and source_crs.",
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="REJECTED",
            entity_type="PARCEL",
            entity_id=op.entity_id,
            error=SyncErrorDetail(
                code="SYNC_OPERATION_INVALID",
                message="Payload must include valid geometry and source_crs.",
            ),
        )

    parcel = session.get(Parcel, op.entity_id)
    if parcel is None or parcel.project_id != project_id:
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="REJECTED",
            error_code="SYNC_ENTITY_NOT_FOUND",
            error_message="The target parcel was not found in this project.",
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="REJECTED",
            entity_type="PARCEL",
            entity_id=op.entity_id,
            error=SyncErrorDetail(
                code="SYNC_ENTITY_NOT_FOUND",
                message="The target parcel was not found in this project.",
            ),
        )

    if parcel.current_geometry_version != op.base_version:
        conflict_dict = {
            "conflict_type": "VERSION_MISMATCH",
            "expected_version": op.base_version,
            "server_version": parcel.current_geometry_version,
            "message": (
                f"This parcel has newer geometry version {parcel.current_geometry_version}. "
                f"Base version was {op.base_version}."
            ),
        }
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="CONFLICT",
            conflict=conflict_dict,
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="CONFLICT",
            entity_type="PARCEL",
            entity_id=op.entity_id,
            server_version=parcel.current_geometry_version,
            conflict=SyncConflictDetail(**conflict_dict),
        )

    try:
        version, result = create_human_parcel_version(
            session,
            parcel,
            user.id,
            geometry,
            source_crs,
            op.base_version,
            change_reason,
        )
    except ParcelVersionConflict as error:
        conflict_dict = {
            "conflict_type": "VERSION_MISMATCH",
            "expected_version": op.base_version,
            "server_version": parcel.current_geometry_version,
            "message": str(error),
        }
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="CONFLICT",
            conflict=conflict_dict,
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="CONFLICT",
            entity_type="PARCEL",
            entity_id=op.entity_id,
            server_version=parcel.current_geometry_version,
            conflict=SyncConflictDetail(**conflict_dict),
        )
    except GeoAIServiceError as error:
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="REJECTED",
            error_code="SYNC_OPERATION_INVALID",
            error_message=str(error),
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="REJECTED",
            entity_type="PARCEL",
            entity_id=op.entity_id,
            error=SyncErrorDetail(
                code="SYNC_OPERATION_INVALID",
                message=str(error),
            ),
        )

    record_audit(
        session,
        "parcel.geometry_version_created",
        "parcel_geometry_version",
        version.id,
        actor_id=user.id,
        project_id=project_id,
        metadata={
            "parcel_id": str(parcel.id),
            "version": version.version,
            "status": result.status,
            "sync_operation_id": str(op.operation_id),
        },
    )

    _record_sync_change(
        session,
        project_id=project_id,
        entity_type="PARCEL",
        entity_id=parcel.id,
        change_type="UPDATED",
        server_version=version.version,
    )

    result_dict = {
        "parcel_version": version.version,
        "status": result.status,
        "area_m2": version.area_m2,
        "area_sqft": version.area_sqft,
    }

    _persist_sync_operation(
        session,
        operation_id=op.operation_id,
        project_id=project_id,
        user_id=user.id,
        client_id=client_id,
        operation_type=op.operation_type,
        entity_id=op.entity_id,
        base_version=op.base_version,
        payload=op.payload,
        status="APPLIED",
        result=result_dict,
        client_created_at=op.client_created_at,
    )

    return SyncOperationResult(
        operation_id=op.operation_id,
        status="APPLIED",
        entity_type="PARCEL",
        entity_id=op.entity_id,
        server_version=version.version,
        result=result_dict,
    )


def _handle_field_correction_create(
    session: Session,
    *,
    project_id: uuid.UUID,
    user: User,
    op: SyncOperationItemRequest,
    client_id: str | None,
) -> SyncOperationResult:
    corrected_value = op.payload.get("corrected_value")
    reason = op.payload.get("reason")

    if not isinstance(corrected_value, str) or not corrected_value.strip() or not isinstance(reason, str) or not reason.strip():
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="REJECTED",
            error_code="SYNC_OPERATION_INVALID",
            error_message="corrected_value and reason are mandatory non-empty strings.",
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="REJECTED",
            entity_type="DOCUMENT_FIELD",
            entity_id=op.entity_id,
            error=SyncErrorDetail(
                code="SYNC_OPERATION_INVALID",
                message="corrected_value and reason are mandatory non-empty strings.",
            ),
        )

    field = session.get(DocumentExtractedField, op.entity_id)
    if field is None:
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="REJECTED",
            error_code="SYNC_ENTITY_NOT_FOUND",
            error_message="The extracted field was not found.",
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="REJECTED",
            entity_type="DOCUMENT_FIELD",
            entity_id=op.entity_id,
            error=SyncErrorDetail(
                code="SYNC_ENTITY_NOT_FOUND",
                message="The extracted field was not found.",
            ),
        )

    doc = session.get(Document, field.document_id)
    if doc is None or doc.project_id != project_id:
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="REJECTED",
            error_code="SYNC_ENTITY_NOT_FOUND",
            error_message="The document was not found in this project.",
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="REJECTED",
            entity_type="DOCUMENT_FIELD",
            entity_id=op.entity_id,
            error=SyncErrorDetail(
                code="SYNC_ENTITY_NOT_FOUND",
                message="The document was not found in this project.",
            ),
        )

    correction = create_correction(
        session,
        document=doc,
        field=field,
        value=corrected_value.strip(),
        reason=reason.strip(),
        actor_id=user.id,
    )
    try:
        queue_correction_revalidation(
            session, document=doc, correction=correction, actor_id=user.id
        )
    except Exception:
        # Revalidation queue failure should not roll back valid field correction
        pass

    _record_sync_change(
        session,
        project_id=project_id,
        entity_type="DOCUMENT_FIELD",
        entity_id=field.id,
        change_type="UPDATED",
        server_version=correction.version,
    )

    result_dict = {
        "correction_id": str(correction.id),
        "version": correction.version,
        "corrected_value": correction.corrected_value,
        "field_name": field.field_name,
        "document_id": str(doc.id),
    }

    _persist_sync_operation(
        session,
        operation_id=op.operation_id,
        project_id=project_id,
        user_id=user.id,
        client_id=client_id,
        operation_type=op.operation_type,
        entity_id=op.entity_id,
        base_version=op.base_version,
        payload=op.payload,
        status="APPLIED",
        result=result_dict,
        client_created_at=op.client_created_at,
    )

    return SyncOperationResult(
        operation_id=op.operation_id,
        status="APPLIED",
        entity_type="DOCUMENT_FIELD",
        entity_id=op.entity_id,
        server_version=correction.version,
        result=result_dict,
    )


def _handle_review_task_update(
    session: Session,
    *,
    project_id: uuid.UUID,
    user: User,
    op: SyncOperationItemRequest,
    client_id: str | None,
) -> SyncOperationResult:
    task = session.get(ReviewTask, op.entity_id)
    if task is None or task.project_id != project_id:
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="REJECTED",
            error_code="SYNC_ENTITY_NOT_FOUND",
            error_message="The review task was not found in this project.",
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="REJECTED",
            entity_type="REVIEW_TASK",
            entity_id=op.entity_id,
            error=SyncErrorDetail(
                code="SYNC_ENTITY_NOT_FOUND",
                message="The review task was not found in this project.",
            ),
        )

    action = op.payload.get("action")
    raw_assignee = op.payload.get("assignee_user_id")
    reason = op.payload.get("reason")
    correction_reference = op.payload.get("correction_reference")
    raw_reprocess = op.payload.get("reprocess_job_id")

    assignee_user_id: uuid.UUID | None = None
    if raw_assignee:
        try:
            assignee_user_id = uuid.UUID(str(raw_assignee))
        except (ValueError, TypeError):
            _persist_sync_operation(
                session,
                operation_id=op.operation_id,
                project_id=project_id,
                user_id=user.id,
                client_id=client_id,
                operation_type=op.operation_type,
                entity_id=op.entity_id,
                base_version=op.base_version,
                payload=op.payload,
                status="REJECTED",
                error_code="SYNC_OPERATION_INVALID",
                error_message="Invalid assignee_user_id UUID.",
                client_created_at=op.client_created_at,
            )
            return SyncOperationResult(
                operation_id=op.operation_id,
                status="REJECTED",
                entity_type="REVIEW_TASK",
                entity_id=op.entity_id,
                error=SyncErrorDetail(
                    code="SYNC_OPERATION_INVALID",
                    message="Invalid assignee_user_id UUID.",
                ),
            )

    reprocess_job_id: uuid.UUID | None = None
    if raw_reprocess:
        try:
            reprocess_job_id = uuid.UUID(str(raw_reprocess))
        except (ValueError, TypeError):
            _persist_sync_operation(
                session,
                operation_id=op.operation_id,
                project_id=project_id,
                user_id=user.id,
                client_id=client_id,
                operation_type=op.operation_type,
                entity_id=op.entity_id,
                base_version=op.base_version,
                payload=op.payload,
                status="REJECTED",
                error_code="SYNC_OPERATION_INVALID",
                error_message="Invalid reprocess_job_id UUID.",
                client_created_at=op.client_created_at,
            )
            return SyncOperationResult(
                operation_id=op.operation_id,
                status="REJECTED",
                entity_type="REVIEW_TASK",
                entity_id=op.entity_id,
                error=SyncErrorDetail(
                    code="SYNC_OPERATION_INVALID",
                    message="Invalid reprocess_job_id UUID.",
                ),
            )

    # Check terminal state idempotency or conflict
    if task.status != "OPEN":
        if action and task.resolution_action == str(action).upper():
            result_dict = {
                "task_id": str(task.id),
                "status": task.status,
                "resolution_action": task.resolution_action,
                "assignee_user_id": str(task.assignee_user_id) if task.assignee_user_id else None,
            }
            _persist_sync_operation(
                session,
                operation_id=op.operation_id,
                project_id=project_id,
                user_id=user.id,
                client_id=client_id,
                operation_type=op.operation_type,
                entity_id=op.entity_id,
                base_version=op.base_version,
                payload=op.payload,
                status="APPLIED",
                result=result_dict,
                client_created_at=op.client_created_at,
            )
            return SyncOperationResult(
                operation_id=op.operation_id,
                status="APPLIED",
                entity_type="REVIEW_TASK",
                entity_id=op.entity_id,
                result=result_dict,
            )

        conflict_dict = {
            "conflict_type": "STATE_CONFLICT",
            "server_state": task.status,
            "message": f"Review task is already in terminal state {task.status}.",
        }
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="CONFLICT",
            conflict=conflict_dict,
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="CONFLICT",
            entity_type="REVIEW_TASK",
            entity_id=op.entity_id,
            conflict=SyncConflictDetail(**conflict_dict),
        )

    try:
        update_review_task(
            session,
            task,
            actor=user,
            action=action,
            assignee_user_id=assignee_user_id,
            reason=reason,
            correction_reference=correction_reference,
            reprocess_job_id=reprocess_job_id,
        )
    except ReviewWorkflowError as error:
        err_msg = str(error)
        if "Resolved review tasks" in err_msg or "Approval is blocked" in err_msg:
            conflict_dict = {
                "conflict_type": "STATE_CONFLICT",
                "server_state": task.status,
                "message": err_msg,
            }
            _persist_sync_operation(
                session,
                operation_id=op.operation_id,
                project_id=project_id,
                user_id=user.id,
                client_id=client_id,
                operation_type=op.operation_type,
                entity_id=op.entity_id,
                base_version=op.base_version,
                payload=op.payload,
                status="CONFLICT",
                conflict=conflict_dict,
                client_created_at=op.client_created_at,
            )
            return SyncOperationResult(
                operation_id=op.operation_id,
                status="CONFLICT",
                entity_type="REVIEW_TASK",
                entity_id=op.entity_id,
                conflict=SyncConflictDetail(**conflict_dict),
            )
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="REJECTED",
            error_code="SYNC_OPERATION_INVALID",
            error_message=err_msg,
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="REJECTED",
            entity_type="REVIEW_TASK",
            entity_id=op.entity_id,
            error=SyncErrorDetail(
                code="SYNC_OPERATION_INVALID",
                message=err_msg,
            ),
        )

    _record_sync_change(
        session,
        project_id=project_id,
        entity_type="REVIEW_TASK",
        entity_id=task.id,
        change_type=task.status,
    )

    result_dict = {
        "task_id": str(task.id),
        "status": task.status,
        "resolution_action": task.resolution_action,
        "assignee_user_id": str(task.assignee_user_id) if task.assignee_user_id else None,
    }

    _persist_sync_operation(
        session,
        operation_id=op.operation_id,
        project_id=project_id,
        user_id=user.id,
        client_id=client_id,
        operation_type=op.operation_type,
        entity_id=op.entity_id,
        base_version=op.base_version,
        payload=op.payload,
        status="APPLIED",
        result=result_dict,
        client_created_at=op.client_created_at,
    )

    return SyncOperationResult(
        operation_id=op.operation_id,
        status="APPLIED",
        entity_type="REVIEW_TASK",
        entity_id=op.entity_id,
        result=result_dict,
    )


def _handle_record_link_resolve(
    session: Session,
    *,
    project_id: uuid.UUID,
    user: User,
    op: SyncOperationItemRequest,
    client_id: str | None,
) -> SyncOperationResult:
    link = session.get(RecordParcelLink, op.entity_id)
    if link is None or link.project_id != project_id:
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="REJECTED",
            error_code="SYNC_ENTITY_NOT_FOUND",
            error_message="The record-parcel link was not found in this project.",
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="REJECTED",
            entity_type="RECORD_PARCEL_LINK",
            entity_id=op.entity_id,
            error=SyncErrorDetail(
                code="SYNC_ENTITY_NOT_FOUND",
                message="The record-parcel link was not found in this project.",
            ),
        )

    action = str(op.payload.get("action") or "").upper()
    reason = op.payload.get("reason")

    if action not in {"CONFIRM", "REJECT"}:
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="REJECTED",
            error_code="SYNC_OPERATION_INVALID",
            error_message="Action must be CONFIRM or REJECT.",
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="REJECTED",
            entity_type="RECORD_PARCEL_LINK",
            entity_id=op.entity_id,
            error=SyncErrorDetail(
                code="SYNC_OPERATION_INVALID",
                message="Action must be CONFIRM or REJECT.",
            ),
        )

    # Check terminal idempotency or conflict
    if link.link_status in {"CONFIRMED", "REJECTED"}:
        if (action == "CONFIRM" and link.link_status == "CONFIRMED") or (
            action == "REJECT" and link.link_status == "REJECTED"
        ):
            result_dict = {
                "link_id": str(link.id),
                "link_status": link.link_status,
                "parcel_id": str(link.parcel_id),
            }
            _persist_sync_operation(
                session,
                operation_id=op.operation_id,
                project_id=project_id,
                user_id=user.id,
                client_id=client_id,
                operation_type=op.operation_type,
                entity_id=op.entity_id,
                base_version=op.base_version,
                payload=op.payload,
                status="APPLIED",
                result=result_dict,
                client_created_at=op.client_created_at,
            )
            return SyncOperationResult(
                operation_id=op.operation_id,
                status="APPLIED",
                entity_type="RECORD_PARCEL_LINK",
                entity_id=op.entity_id,
                result=result_dict,
            )

        conflict_dict = {
            "conflict_type": "STATE_CONFLICT",
            "server_state": link.link_status,
            "message": f"Link has already been resolved as {link.link_status}.",
        }
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="CONFLICT",
            conflict=conflict_dict,
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="CONFLICT",
            entity_type="RECORD_PARCEL_LINK",
            entity_id=op.entity_id,
            conflict=SyncConflictDetail(**conflict_dict),
        )

    try:
        resolve_link(
            session,
            link=link,
            actor=user,
            action=action,
            reason=reason,
        )
    except RecordParcelLinkError as error:
        err_msg = str(error)
        if "already has a confirmed parcel association" in err_msg or "Only pending" in err_msg:
            conflict_dict = {
                "conflict_type": "STATE_CONFLICT",
                "server_state": link.link_status,
                "message": err_msg,
            }
            _persist_sync_operation(
                session,
                operation_id=op.operation_id,
                project_id=project_id,
                user_id=user.id,
                client_id=client_id,
                operation_type=op.operation_type,
                entity_id=op.entity_id,
                base_version=op.base_version,
                payload=op.payload,
                status="CONFLICT",
                conflict=conflict_dict,
                client_created_at=op.client_created_at,
            )
            return SyncOperationResult(
                operation_id=op.operation_id,
                status="CONFLICT",
                entity_type="RECORD_PARCEL_LINK",
                entity_id=op.entity_id,
                conflict=SyncConflictDetail(**conflict_dict),
            )
        _persist_sync_operation(
            session,
            operation_id=op.operation_id,
            project_id=project_id,
            user_id=user.id,
            client_id=client_id,
            operation_type=op.operation_type,
            entity_id=op.entity_id,
            base_version=op.base_version,
            payload=op.payload,
            status="REJECTED",
            error_code="SYNC_OPERATION_INVALID",
            error_message=err_msg,
            client_created_at=op.client_created_at,
        )
        return SyncOperationResult(
            operation_id=op.operation_id,
            status="REJECTED",
            entity_type="RECORD_PARCEL_LINK",
            entity_id=op.entity_id,
            error=SyncErrorDetail(
                code="SYNC_OPERATION_INVALID",
                message=err_msg,
            ),
        )

    _record_sync_change(
        session,
        project_id=project_id,
        entity_type="RECORD_PARCEL_LINK",
        entity_id=link.id,
        change_type=link.link_status,
    )

    result_dict = {
        "link_id": str(link.id),
        "link_status": link.link_status,
        "parcel_id": str(link.parcel_id),
    }

    _persist_sync_operation(
        session,
        operation_id=op.operation_id,
        project_id=project_id,
        user_id=user.id,
        client_id=client_id,
        operation_type=op.operation_type,
        entity_id=op.entity_id,
        base_version=op.base_version,
        payload=op.payload,
        status="APPLIED",
        result=result_dict,
        client_created_at=op.client_created_at,
    )

    return SyncOperationResult(
        operation_id=op.operation_id,
        status="APPLIED",
        entity_type="RECORD_PARCEL_LINK",
        entity_id=op.entity_id,
        result=result_dict,
    )


def apply_sync_batch(
    session: Session,
    *,
    project_id: uuid.UUID,
    user: User,
    batch: SyncBatchRequest,
) -> SyncBatchResponse:
    """Execute sync batch with per-operation isolation, idempotency, and savepoints."""
    if len(batch.operations) > MAX_SYNC_BATCH_SIZE:
        raise ApiError(
            422,
            "SYNC_BATCH_TOO_LARGE",
            f"Batch size {len(batch.operations)} exceeds hard limit of {MAX_SYNC_BATCH_SIZE}.",
        )

    caller_permissions = user_permissions(session, user.id)
    results: list[SyncOperationResult] = []

    for op in batch.operations:
        # Step 1: Check if already processed (global idempotency)
        existing = session.get(SyncOperation, op.operation_id)
        if existing is not None:
            server_version = None
            if existing.result_json:
                server_version = (
                    existing.result_json.get("parcel_version")
                    or existing.result_json.get("version")
                    or existing.result_json.get("server_version")
                )
            results.append(
                SyncOperationResult(
                    operation_id=existing.id,
                    status="DUPLICATE",
                    entity_type=OPERATION_ENTITY_TYPES.get(existing.operation_type),
                    entity_id=existing.entity_id,
                    server_version=server_version,
                    result=existing.result_json,
                    conflict=SyncConflictDetail(**existing.conflict_json)
                    if existing.conflict_json
                    else None,
                    error=SyncErrorDetail(
                        code=existing.error_code or "DUPLICATE_OPERATION",
                        message=existing.error_message or "Operation already processed.",
                    )
                    if existing.error_code
                    else None,
                )
            )
            continue

        # Step 2: Validate operation type support
        if op.operation_type not in SUPPORTED_OPERATIONS:
            _persist_sync_operation(
                session,
                operation_id=op.operation_id,
                project_id=project_id,
                user_id=user.id,
                client_id=batch.client_id,
                operation_type=op.operation_type,
                entity_id=op.entity_id,
                base_version=op.base_version,
                payload=op.payload,
                status="REJECTED",
                error_code="SYNC_OPERATION_UNSUPPORTED",
                error_message=f"Operation type '{op.operation_type}' is not supported in offline sync.",
                client_created_at=op.client_created_at,
            )
            results.append(
                SyncOperationResult(
                    operation_id=op.operation_id,
                    status="REJECTED",
                    entity_type=None,
                    entity_id=op.entity_id,
                    error=SyncErrorDetail(
                        code="SYNC_OPERATION_UNSUPPORTED",
                        message=f"Operation type '{op.operation_type}' is not supported in offline sync.",
                    ),
                )
            )
            continue

        # Step 3: Check required permission
        required_perm = OPERATION_PERMISSIONS.get(op.operation_type)
        if required_perm and required_perm not in caller_permissions:
            _persist_sync_operation(
                session,
                operation_id=op.operation_id,
                project_id=project_id,
                user_id=user.id,
                client_id=batch.client_id,
                operation_type=op.operation_type,
                entity_id=op.entity_id,
                base_version=op.base_version,
                payload=op.payload,
                status="REJECTED",
                error_code="SYNC_PERMISSION_DENIED",
                error_message=f"Missing required permission '{required_perm}'.",
                client_created_at=op.client_created_at,
            )
            results.append(
                SyncOperationResult(
                    operation_id=op.operation_id,
                    status="REJECTED",
                    entity_type=OPERATION_ENTITY_TYPES.get(op.operation_type),
                    entity_id=op.entity_id,
                    error=SyncErrorDetail(
                        code="SYNC_PERMISSION_DENIED",
                        message=f"Missing required permission '{required_perm}'.",
                    ),
                )
            )
            continue

        # Step 4: Execute inside savepoint/nested transaction
        try:
            with session.begin_nested():
                if op.operation_type == "PARCEL_VERSION_CREATE":
                    res = _handle_parcel_version_create(
                        session,
                        project_id=project_id,
                        user=user,
                        op=op,
                        client_id=batch.client_id,
                    )
                elif op.operation_type == "FIELD_CORRECTION_CREATE":
                    res = _handle_field_correction_create(
                        session,
                        project_id=project_id,
                        user=user,
                        op=op,
                        client_id=batch.client_id,
                    )
                elif op.operation_type == "REVIEW_TASK_UPDATE":
                    res = _handle_review_task_update(
                        session,
                        project_id=project_id,
                        user=user,
                        op=op,
                        client_id=batch.client_id,
                    )
                elif op.operation_type == "RECORD_LINK_RESOLVE":
                    res = _handle_record_link_resolve(
                        session,
                        project_id=project_id,
                        user=user,
                        op=op,
                        client_id=batch.client_id,
                    )
                else:
                    res = SyncOperationResult(
                        operation_id=op.operation_id,
                        status="REJECTED",
                        entity_id=op.entity_id,
                        error=SyncErrorDetail(
                            code="SYNC_OPERATION_UNSUPPORTED",
                            message="Unsupported operation.",
                        ),
                    )
                results.append(res)
        except Exception as error:
            # Fallback for unexpected exceptions inside savepoint
            _persist_sync_operation(
                session,
                operation_id=op.operation_id,
                project_id=project_id,
                user_id=user.id,
                client_id=batch.client_id,
                operation_type=op.operation_type,
                entity_id=op.entity_id,
                base_version=op.base_version,
                payload=op.payload,
                status="REJECTED",
                error_code="SYNC_OPERATION_FAILED",
                error_message=str(error),
                client_created_at=op.client_created_at,
            )
            results.append(
                SyncOperationResult(
                    operation_id=op.operation_id,
                    status="REJECTED",
                    entity_type=OPERATION_ENTITY_TYPES.get(op.operation_type),
                    entity_id=op.entity_id,
                    error=SyncErrorDetail(
                        code="SYNC_OPERATION_FAILED",
                        message=str(error),
                    ),
                )
            )

    latest_cursor = (
        session.scalar(
            select(func.max(SyncChange.id)).where(SyncChange.project_id == project_id)
        )
        or 0
    )

    return SyncBatchResponse(
        project_id=project_id,
        server_cursor=str(latest_cursor),
        results=results,
    )


def get_sync_changes(
    session: Session,
    *,
    project_id: uuid.UUID,
    cursor: str | None = None,
    limit: int = 50,
) -> SyncChangesResponse:
    """Bounded, monotonic project change feed pagination without sensitive leaks."""
    cursor_val = 0
    if cursor is not None:
        try:
            cursor_val = int(cursor)
        except (ValueError, TypeError):
            cursor_val = 0

    bounded_limit = max(1, min(limit, 100))

    query = (
        select(SyncChange)
        .where(
            SyncChange.project_id == project_id,
            SyncChange.id > cursor_val,
        )
        .order_by(SyncChange.id.asc())
        .limit(bounded_limit + 1)
    )

    rows = list(session.scalars(query))
    has_more = len(rows) > bounded_limit
    items_slice = rows[:bounded_limit] if has_more else rows

    items = [
        SyncChangeItem(
            cursor=str(change.id),
            entity_type=change.entity_type,
            entity_id=change.entity_id,
            change_type=change.change_type,
            server_version=change.server_version,
            changed_at=change.changed_at,
        )
        for change in items_slice
    ]

    next_cursor = items[-1].cursor if items else str(cursor_val)

    return SyncChangesResponse(
        items=items,
        next_cursor=next_cursor,
        has_more=has_more,
    )


def get_sync_operation_by_id(
    session: Session,
    *,
    project_id: uuid.UUID,
    operation_id: uuid.UUID,
) -> SyncOperationDetailResponse:
    """Retrieve persisted operation result for client recovery."""
    op = session.get(SyncOperation, operation_id)
    if op is None or op.project_id != project_id:
        raise not_found("SYNC_OPERATION_NOT_FOUND", "The requested sync operation was not found.")

    return SyncOperationDetailResponse(
        id=op.id,
        project_id=op.project_id,
        user_id=op.user_id,
        client_id=op.client_id,
        operation_type=op.operation_type,
        entity_id=op.entity_id,
        base_version=op.base_version,
        status=op.status,
        result=op.result_json,
        conflict=op.conflict_json,
        error_code=op.error_code,
        error_message=op.error_message,
        client_created_at=op.client_created_at,
        applied_at=op.applied_at,
        created_at=op.created_at,
    )

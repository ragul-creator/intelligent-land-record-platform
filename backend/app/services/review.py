"""Phase E.2 review queue persistence and workflow rules."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.core.auth import user_permissions
from app.models import ProcessingJob, ProjectMember, ReviewTask, User


REVIEW_ACTIONS = frozenset({"APPROVE", "CORRECT", "REJECT", "REPROCESS", "COMMENT", "ESCALATE"})


class ReviewWorkflowError(ValueError):
    """Raised when a reviewer action violates the canonical human-review policy."""


def create_review_task(
    session: Session,
    *,
    project_id: uuid.UUID,
    queue_type: str,
    target_type: str,
    target_id: uuid.UUID,
    severity: str,
    summary: str,
    source_refs: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    blocking_issue_count: int = 0,
    created_by_user_id: uuid.UUID | None = None,
) -> ReviewTask:
    """Create one auditable review work item from validation/processing code."""
    queue_type = queue_type.upper()
    severity = severity.upper()
    if queue_type not in {"DOCUMENT", "GIS"}:
        raise ReviewWorkflowError("queue_type must be DOCUMENT or GIS.")
    if severity not in {"INFO", "LOW", "MEDIUM", "HIGH"}:
        raise ReviewWorkflowError("severity must be INFO, LOW, MEDIUM, or HIGH.")
    if blocking_issue_count < 0:
        raise ReviewWorkflowError("blocking_issue_count must be non-negative.")
    if not summary.strip():
        raise ReviewWorkflowError("summary must not be empty.")

    task = ReviewTask(
        project_id=project_id,
        queue_type=queue_type,
        target_type=target_type,
        target_id=target_id,
        severity=severity,
        summary=summary.strip(),
        source_refs_json=source_refs or [],
        metadata_json=metadata or {},
        blocking_issue_count=blocking_issue_count,
        created_by_user_id=created_by_user_id,
    )
    session.add(task)
    session.flush()
    record_audit(
        session,
        "review.task_created",
        "review_task",
        task.id,
        actor_id=created_by_user_id,
        project_id=project_id,
        metadata={
            "queue_type": queue_type,
            "severity": severity,
            "target_type": target_type,
            "target_id": str(target_id),
            "blocking_issue_count": blocking_issue_count,
        },
    )
    return task


def _review_capable_assignee(
    session: Session,
    *,
    project_id: uuid.UUID,
    assignee_user_id: uuid.UUID,
) -> User:
    user = session.get(User, assignee_user_id)
    membership = session.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == assignee_user_id,
        )
    )
    if user is None or membership is None or "review:act" not in user_permissions(session, assignee_user_id):
        raise ReviewWorkflowError("Assignee must be a project member with review:act permission.")
    return user


def assign_review_task(
    session: Session,
    task: ReviewTask,
    *,
    actor: User,
    assignee_user_id: uuid.UUID,
    reason: str | None = None,
    escalated: bool = False,
) -> None:
    """Assign or escalate a task only to a project member allowed to review."""
    if task.status != "OPEN":
        raise ReviewWorkflowError("Resolved review tasks cannot be changed.")
    _review_capable_assignee(session, project_id=task.project_id, assignee_user_id=assignee_user_id)
    previous = task.assignee_user_id
    task.assignee_user_id = assignee_user_id
    if escalated:
        task.escalated = True
    record_audit(
        session,
        "review.task_escalated" if escalated else "review.task_assigned",
        "review_task",
        task.id,
        actor_id=actor.id,
        project_id=task.project_id,
        metadata={
            "previous_assignee_user_id": str(previous) if previous else None,
            "assignee_user_id": str(assignee_user_id),
            "reason": reason,
        },
    )


def apply_review_action(
    session: Session,
    task: ReviewTask,
    *,
    actor: User,
    action: str,
    reason: str | None = None,
    assignee_user_id: uuid.UUID | None = None,
    correction_reference: str | None = None,
    reprocess_job_id: uuid.UUID | None = None,
) -> None:
    """Apply one canonical review action and preserve it through the audit log."""
    action = action.upper()
    if action not in REVIEW_ACTIONS:
        raise ReviewWorkflowError("Unsupported review action.")
    if task.status != "OPEN":
        raise ReviewWorkflowError("Resolved review tasks cannot be actioned again.")

    if action == "APPROVE":
        if task.blocking_issue_count > 0:
            raise ReviewWorkflowError("Approval is blocked while high-severity issues remain unresolved.")
        task.status = "RESOLVED"
        task.resolution_action = action
        task.resolved_at = datetime.now(UTC)
        task.resolved_by_user_id = actor.id

    elif action == "CORRECT":
        if not reason or not reason.strip():
            raise ReviewWorkflowError("Correct requires a reason.")
        if not correction_reference or not correction_reference.strip():
            raise ReviewWorkflowError("Correct requires a reference to the versioned correction.")
        # Revalidation is required after a correction, so the task stays OPEN.

    elif action == "REJECT":
        if not reason or not reason.strip():
            raise ReviewWorkflowError("Reject requires a reason.")
        task.status = "RESOLVED"
        task.resolution_action = action
        task.resolved_at = datetime.now(UTC)
        task.resolved_by_user_id = actor.id

    elif action == "REPROCESS":
        if not reason or not reason.strip():
            raise ReviewWorkflowError("Reprocess requires a reason.")
        if reprocess_job_id is None:
            raise ReviewWorkflowError("Reprocess requires the new processing job ID.")
        job = session.get(ProcessingJob, reprocess_job_id)
        if job is None or job.project_id != task.project_id:
            raise ReviewWorkflowError("Reprocess job must exist in the same project.")
        task.status = "RESOLVED"
        task.resolution_action = action
        task.resolved_at = datetime.now(UTC)
        task.resolved_by_user_id = actor.id

    elif action == "COMMENT":
        if not reason or not reason.strip():
            raise ReviewWorkflowError("Comment requires text in reason.")

    elif action == "ESCALATE":
        if not reason or not reason.strip():
            raise ReviewWorkflowError("Escalate requires a reason.")
        if assignee_user_id is None:
            raise ReviewWorkflowError("Escalate requires an assignee.")
        assign_review_task(
            session,
            task,
            actor=actor,
            assignee_user_id=assignee_user_id,
            reason=reason,
            escalated=True,
        )

    record_audit(
        session,
        "review.action_applied",
        "review_task",
        task.id,
        actor_id=actor.id,
        project_id=task.project_id,
        metadata={
            "action": action,
            "reason": reason,
            "correction_reference": correction_reference,
            "reprocess_job_id": str(reprocess_job_id) if reprocess_job_id else None,
            "assignee_user_id": str(assignee_user_id) if assignee_user_id else None,
            "status": task.status,
        },
    )


def update_review_task(
    session: Session,
    task: ReviewTask,
    *,
    actor: User,
    action: str | None,
    assignee_user_id: uuid.UUID | None,
    reason: str | None,
    correction_reference: str | None,
    reprocess_job_id: uuid.UUID | None,
) -> None:
    """Apply assignment and/or reviewer action in one PATCH transaction."""
    if task.status != "OPEN":
        raise ReviewWorkflowError("Resolved review tasks cannot be changed.")

    if action == "ESCALATE":
        apply_review_action(
            session,
            task,
            actor=actor,
            action=action,
            reason=reason,
            assignee_user_id=assignee_user_id,
            correction_reference=correction_reference,
            reprocess_job_id=reprocess_job_id,
        )
        return

    if assignee_user_id is not None:
        assign_review_task(
            session,
            task,
            actor=actor,
            assignee_user_id=assignee_user_id,
            reason=reason,
        )

    if action is not None:
        apply_review_action(
            session,
            task,
            actor=actor,
            action=action,
            reason=reason,
            correction_reference=correction_reference,
            reprocess_job_id=reprocess_job_id,
        )

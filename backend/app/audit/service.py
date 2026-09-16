"""Safe audit persistence for security-sensitive backend actions."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog


def record_audit(
    session: Session,
    action: str,
    target_type: str,
    target_id: uuid.UUID | None = None,
    actor_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Record identifiers and safe metadata only; never pass credentials or tokens."""
    session.add(
        AuditLog(
            action=action,
            target_type=target_type,
            target_id=target_id,
            actor_id=actor_id,
            project_id=project_id,
            metadata_json=metadata or {},
        )
    )

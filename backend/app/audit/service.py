"""Safe audit persistence for security-sensitive backend actions."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models import AuditLog

_SENSITIVE_METADATA_TERMS = (
    "password",
    "token",
    "secret",
    "credential",
    "authorization",
    "storage_key",
    "signed_url",
)


def sanitize_audit_metadata(value: Any) -> Any:
    """Remove credential-like values before audit persistence or API serialization."""
    if isinstance(value, dict):
        return {
            str(key): sanitize_audit_metadata(item)
            for key, item in value.items()
            if not any(term in str(key).lower() for term in _SENSITIVE_METADATA_TERMS)
        }
    if isinstance(value, list):
        return [sanitize_audit_metadata(item) for item in value]
    if isinstance(value, str) and ("X-Amz-Signature=" in value or "X-Amz-Credential=" in value):
        return "[redacted]"
    return value


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
            metadata_json=sanitize_audit_metadata(metadata or {}),
        )
    )

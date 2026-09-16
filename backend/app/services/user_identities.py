"""Human-facing user identifier helpers backed by PostgreSQL allocation."""

import re

from sqlalchemy import text
from sqlalchemy.orm import Session


LOGIN_ID_SEQUENCE = "users_login_id_seq"
ROLE_LOGIN_PREFIXES = {
    "ADMIN": "ADM",
    "OFFICER": "OFF",
    "REVIEWER": "REV",
    "SURVEYOR": "SUR",
    "VIEWER": "VWR",
}
LOGIN_ID_PATTERN = re.compile(r"^(ADM|OFF|REV|SUR|VWR)-TN-[0-9]{6,}$")


def login_prefix_for_role(role_name: str) -> str:
    """Return the display prefix for an approved application role."""
    try:
        return ROLE_LOGIN_PREFIXES[role_name.strip().upper()]
    except KeyError as error:
        raise ValueError("An approved application role is required for login ID generation.") from error


def normalize_login_id(login_id: str) -> str:
    """Normalize and validate a supplied immutable login identifier."""
    normalized = login_id.strip().upper()
    if len(normalized) > 32 or LOGIN_ID_PATTERN.fullmatch(normalized) is None:
        raise ValueError("Login ID must use an approved PREFIX-TN-SEQUENCE format.")
    return normalized


def validate_login_id_for_role(login_id: str, role_name: str) -> str:
    """Ensure a supplied identifier uses the prefix for its initial role."""
    normalized = normalize_login_id(login_id)
    if not normalized.startswith(f"{login_prefix_for_role(role_name)}-TN-"):
        raise ValueError("Login ID prefix must match the initial application role.")
    return normalized


def generate_login_id(session: Session, role_name: str) -> str:
    """Allocate a unique login ID using PostgreSQL's concurrency-safe sequence."""
    sequence_value = session.scalar(text(f"SELECT nextval('{LOGIN_ID_SEQUENCE}')"))
    if sequence_value is None:
        raise RuntimeError("The login ID sequence is unavailable; apply migrations first.")
    return f"{login_prefix_for_role(role_name)}-TN-{sequence_value:06d}"

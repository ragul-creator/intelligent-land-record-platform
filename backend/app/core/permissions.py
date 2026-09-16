"""Canonical application roles, permissions, and deterministic role mappings."""

APPLICATION_ROLES = (
    "ADMIN",
    "OFFICER",
    "REVIEWER",
    "SURVEYOR",
    "VIEWER",
)

PERMISSION_CODES = (
    "project:read",
    "project:create",
    "project:update",
    "project:member_manage",
    "document:upload",
    "document:read",
    "document:process",
    "document:reprocess",
    "field:read",
    "field:correct",
    "record:read",
    "record:publish",
    "validation:run",
    "validation:resolve",
    "review:read",
    "review:act",
    "imagery:upload",
    "geoai:process",
    "geo:read",
    "geo:edit_draft",
    "geo:approve",
    "export:read",
    "dashboard:read",
    "audit:read",
    "user:manage",
    "role:read",
    "role:manage",
    "system:admin",
)

ROLE_PERMISSION_CODES: dict[str, frozenset[str]] = {
    "ADMIN": frozenset(PERMISSION_CODES),
    "OFFICER": frozenset({
        "project:read", "project:create", "project:update", "document:upload", "document:read",
        "document:process", "document:reprocess", "field:read", "field:correct", "record:read",
        "validation:run", "validation:resolve", "review:read", "dashboard:read", "audit:read",
        "export:read", "geo:read", "imagery:upload",
    }),
    "REVIEWER": frozenset({
        "project:read", "document:read", "field:read", "field:correct", "record:read",
        "validation:run", "validation:resolve", "review:read", "review:act", "geo:read",
        "geo:approve", "dashboard:read", "export:read",
    }),
    "SURVEYOR": frozenset({
        "project:read", "document:read", "record:read", "imagery:upload", "geoai:process",
        "geo:read", "geo:edit_draft", "dashboard:read", "export:read",
    }),
    "VIEWER": frozenset({
        "project:read", "document:read", "record:read", "geo:read", "dashboard:read", "export:read",
    }),
}

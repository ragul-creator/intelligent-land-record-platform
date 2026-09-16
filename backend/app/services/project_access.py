"""Shared authorization-aware project lookup helpers."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.auth import user_permissions, user_roles
from app.core.errors import forbidden, not_found
from app.models import Project, ProjectMember, User


def get_project_for_user(
    session: Session,
    user: User,
    project_id: uuid.UUID,
    permission: str,
) -> Project:
    """Return a project only when the caller has its global permission and membership."""
    if permission not in user_permissions(session, user.id):
        raise forbidden("PROJECT_FORBIDDEN", "You are not allowed to access this project.")
    project = session.scalar(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(Project.id == project_id, ProjectMember.user_id == user.id)
    )
    if project is None:
        # Do not distinguish a missing project from one outside the caller's scope.
        raise not_found("PROJECT_NOT_FOUND", "The requested project was not found.")
    return project


def get_effective_project_role(session: Session, user: User) -> str:
    """Choose a deterministic existing application role for a new owner membership."""
    from app.core.permissions import APPLICATION_ROLES

    assigned_roles = set(user_roles(session, user.id))
    for role_name in APPLICATION_ROLES:
        if role_name in assigned_roles:
            return role_name
    raise forbidden("PROJECT_FORBIDDEN", "You are not allowed to create a project.")

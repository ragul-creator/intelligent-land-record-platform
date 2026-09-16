"""Project, membership, summary, workflow, and project-audit API endpoints."""

import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit.service import record_audit, sanitize_audit_metadata
from app.core.auth import get_current_user, require_permission, user_roles
from app.core.errors import ApiError, forbidden, not_found
from app.core.database import get_db_session
from app.models import AuditLog, File, ProcessingJob, Project, ProjectMember, Role, User, UserRole
from app.schemas.common import PageMetadata
from app.schemas.projects import (
    AuditLogListResponse,
    AuditLogResponse,
    MemberCreateRequest,
    MemberUpdateRequest,
    ProjectCreateRequest,
    ProjectListResponse,
    ProjectMemberListResponse,
    ProjectMemberResponse,
    ProjectResponse,
    ProjectState,
    ProjectSummaryResponse,
    ProjectUpdateRequest,
    ProjectWorkflowResponse,
    StatusCount,
)
from app.services.project_access import get_effective_project_role, get_project_for_user

router = APIRouter(prefix="/projects", tags=["projects"])

CANONICAL_WORKFLOW_STATES = (
    "UPLOADED",
    "QUEUED",
    "PROCESSING",
    "EXTRACTED",
    "VALIDATING",
    "REVIEW_REQUIRED",
    "VALIDATED",
    "PUBLISHED",
    "FAILED",
)


def _page(limit: int, offset: int, total: int) -> PageMetadata:
    return PageMetadata(limit=limit, offset=offset, total=total)


def _project_response(project: Project) -> ProjectResponse:
    return ProjectResponse(
        id=project.id,
        name=project.name,
        description=project.description,
        state=project.state,
        owner_id=project.owner_id,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


def _member_response(member: ProjectMember, user: User) -> ProjectMemberResponse:
    return ProjectMemberResponse(
        user_id=member.user_id,
        login_id=user.login_id,
        full_name=user.full_name,
        is_active=user.is_active,
        role=member.role,
        created_at=member.created_at,
    )


def _validate_member_target(session: Session, user_id: uuid.UUID, role_name: str) -> User:
    target_user = session.get(User, user_id)
    if target_user is None:
        raise not_found("USER_NOT_FOUND", "The requested user was not found.")
    if not target_user.is_active:
        raise ApiError(status.HTTP_409_CONFLICT, "USER_INACTIVE", "Inactive users cannot be added to a project.")
    has_role = session.scalar(
        select(UserRole)
        .join(Role, Role.id == UserRole.role_id)
        .where(UserRole.user_id == target_user.id, Role.name == role_name)
    )
    if has_role is None:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "MEMBER_ROLE_INVALID",
            "The member must already hold the selected application role.",
        )
    return target_user


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    request: ProjectCreateRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(require_permission("project:create")),
) -> ProjectResponse:
    project = Project(
        name=request.name,
        description=request.description,
        state="ACTIVE",
        owner_id=user.id,
    )
    session.add(project)
    try:
        session.flush()
        session.add(
            ProjectMember(
                project_id=project.id,
                user_id=user.id,
                role=get_effective_project_role(session, user),
            )
        )
        record_audit(session, "project.created", "project", project.id, actor_id=user.id, project_id=project.id)
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ApiError(status.HTTP_409_CONFLICT, "PROJECT_CONFLICT", "A project with this name already exists.") from error
    return _project_response(project)


@router.get("", response_model=ProjectListResponse)
def list_projects(
    state: ProjectState | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
    user: User = Depends(require_permission("project:read")),
) -> ProjectListResponse:
    filters = [ProjectMember.user_id == user.id]
    if state is not None:
        filters.append(Project.state == state)
    total = session.scalar(select(func.count(Project.id)).join(ProjectMember).where(*filters)) or 0
    projects = list(
        session.scalars(
            select(Project)
            .join(ProjectMember)
            .where(*filters)
            .order_by(Project.created_at.desc(), Project.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return ProjectListResponse(items=[_project_response(project) for project in projects], page=_page(limit, offset, total))


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ProjectResponse:
    return _project_response(get_project_for_user(session, user, project_id, "project:read"))


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: uuid.UUID,
    request: ProjectUpdateRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ProjectResponse:
    project = get_project_for_user(session, user, project_id, "project:update")
    changes: dict[str, str | None] = {}
    for field_name in request.model_fields_set:
        value = getattr(request, field_name)
        setattr(project, field_name, value)
        changes[field_name] = value
    if not changes:
        return _project_response(project)
    try:
        record_audit(
            session,
            "project.updated",
            "project",
            project.id,
            actor_id=user.id,
            project_id=project.id,
            metadata={"changed_fields": sorted(changes)},
        )
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise ApiError(status.HTTP_409_CONFLICT, "PROJECT_CONFLICT", "A project with this name already exists.") from error
    session.refresh(project)
    return _project_response(project)


@router.get("/{project_id}/members", response_model=ProjectMemberListResponse)
def list_project_members(
    project_id: uuid.UUID,
    active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ProjectMemberListResponse:
    get_project_for_user(session, user, project_id, "project:read")
    filters = [ProjectMember.project_id == project_id]
    if active is not None:
        filters.append(User.is_active == active)
    total = session.scalar(select(func.count(ProjectMember.user_id)).join(User).where(*filters)) or 0
    rows = session.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(*filters)
        .order_by(ProjectMember.created_at, ProjectMember.user_id)
        .limit(limit)
        .offset(offset)
    ).all()
    return ProjectMemberListResponse(
        items=[_member_response(member, member_user) for member, member_user in rows],
        page=_page(limit, offset, total),
    )


@router.post("/{project_id}/members", response_model=ProjectMemberResponse, status_code=status.HTTP_201_CREATED)
def add_project_member(
    project_id: uuid.UUID,
    request: MemberCreateRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ProjectMemberResponse:
    project = get_project_for_user(session, user, project_id, "project:member_manage")
    target_user = _validate_member_target(session, request.user_id, request.role)
    if session.get(ProjectMember, {"project_id": project.id, "user_id": target_user.id}) is not None:
        raise ApiError(status.HTTP_409_CONFLICT, "MEMBERSHIP_EXISTS", "The user is already a project member.")
    member = ProjectMember(project_id=project.id, user_id=target_user.id, role=request.role)
    session.add(member)
    record_audit(
        session,
        "project.member_added",
        "project_member",
        target_user.id,
        actor_id=user.id,
        project_id=project.id,
        metadata={"role": request.role},
    )
    session.commit()
    return _member_response(member, target_user)


@router.patch("/{project_id}/members/{user_id}", response_model=ProjectMemberResponse)
def update_project_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    request: MemberUpdateRequest,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ProjectMemberResponse:
    project = get_project_for_user(session, user, project_id, "project:member_manage")
    if project.owner_id == user_id:
        raise ApiError(status.HTTP_409_CONFLICT, "OWNER_MEMBERSHIP_PROTECTED", "The owner membership cannot be changed.")
    member = session.get(ProjectMember, {"project_id": project.id, "user_id": user_id})
    if member is None:
        raise not_found("MEMBER_NOT_FOUND", "The requested project member was not found.")
    target_user = _validate_member_target(session, user_id, request.role)
    old_role = member.role
    member.role = request.role
    record_audit(
        session,
        "project.member_updated",
        "project_member",
        user_id,
        actor_id=user.id,
        project_id=project.id,
        metadata={"from_role": old_role, "to_role": request.role},
    )
    session.commit()
    return _member_response(member, target_user)


@router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_project_member(
    project_id: uuid.UUID,
    user_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> None:
    project = get_project_for_user(session, user, project_id, "project:member_manage")
    if project.owner_id == user_id:
        raise ApiError(status.HTTP_409_CONFLICT, "OWNER_MEMBERSHIP_PROTECTED", "The owner membership cannot be removed.")
    member = session.get(ProjectMember, {"project_id": project.id, "user_id": user_id})
    if member is None:
        raise not_found("MEMBER_NOT_FOUND", "The requested project member was not found.")
    session.delete(member)
    record_audit(
        session,
        "project.member_removed",
        "project_member",
        user_id,
        actor_id=user.id,
        project_id=project.id,
    )
    session.commit()


@router.get("/{project_id}/summary", response_model=ProjectSummaryResponse)
def get_project_summary(
    project_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ProjectSummaryResponse:
    project = get_project_for_user(session, user, project_id, "project:read")
    file_statuses = [StatusCount(status=value, count=count) for value, count in session.execute(
        select(File.status, func.count(File.id)).where(File.project_id == project.id).group_by(File.status)
    )]
    job_statuses = [StatusCount(status=value, count=count) for value, count in session.execute(
        select(ProcessingJob.status, func.count(ProcessingJob.id))
        .where(ProcessingJob.project_id == project.id)
        .group_by(ProcessingJob.status)
    )]
    return ProjectSummaryResponse(
        project_id=project.id,
        member_count=session.scalar(select(func.count(ProjectMember.user_id)).where(ProjectMember.project_id == project.id)) or 0,
        file_count=sum(item.count for item in file_statuses),
        processing_job_count=sum(item.count for item in job_statuses),
        audit_event_count=session.scalar(select(func.count(AuditLog.id)).where(AuditLog.project_id == project.id)) or 0,
        file_statuses=file_statuses,
        processing_job_statuses=job_statuses,
    )


@router.get("/{project_id}/workflow", response_model=ProjectWorkflowResponse)
def get_project_workflow(
    project_id: uuid.UUID,
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> ProjectWorkflowResponse:
    project = get_project_for_user(session, user, project_id, "project:read")
    observed: list[StatusCount] = []
    uploaded = session.scalar(select(func.count(File.id)).where(File.project_id == project.id, File.status == "UPLOADED")) or 0
    if uploaded:
        observed.append(StatusCount(status="UPLOADED", count=uploaded))
    for job_status, count in session.execute(
        select(ProcessingJob.status, func.count(ProcessingJob.id))
        .where(ProcessingJob.project_id == project.id, ProcessingJob.status.in_(("QUEUED", "PROCESSING", "FAILED")))
        .group_by(ProcessingJob.status)
    ):
        observed.append(StatusCount(status=job_status, count=count))
    return ProjectWorkflowResponse(project_id=project.id, observed_stages=observed, canonical_states=list(CANONICAL_WORKFLOW_STATES))


@router.get("/{project_id}/audit", response_model=AuditLogListResponse)
def list_project_audit_logs(
    project_id: uuid.UUID,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_db_session),
    user: User = Depends(get_current_user),
) -> AuditLogListResponse:
    project = get_project_for_user(session, user, project_id, "audit:read")
    total = session.scalar(select(func.count(AuditLog.id)).where(AuditLog.project_id == project.id)) or 0
    audit_logs = list(
        session.scalars(
            select(AuditLog)
            .where(AuditLog.project_id == project.id)
            .order_by(AuditLog.created_at.desc(), AuditLog.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return AuditLogListResponse(
        items=[
            AuditLogResponse(
                id=audit.id,
                actor_id=audit.actor_id,
                action=audit.action,
                target_type=audit.target_type,
                target_id=audit.target_id,
                metadata=sanitize_audit_metadata(audit.metadata_json),
                created_at=audit.created_at,
            )
            for audit in audit_logs
        ],
        page=_page(limit, offset, total),
    )

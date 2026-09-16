"""Authentication endpoints with revocable, rotating refresh sessions."""

import hashlib
import hmac
import uuid
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.core.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    token_digest,
    user_permissions,
    user_roles,
    utc_now,
    verify_password,
)
from app.core.config import get_settings
from app.core.database import get_db_session
from app.models import AuthSession, ProjectMember, User
from app.schemas.auth import (
    CurrentUserResponse,
    LoginRequest,
    LogoutRequest,
    ProjectMembershipResponse,
    RefreshRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


def _invalid_credentials() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials.")


def _identity_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _token_response(user_id: uuid.UUID, session_id: uuid.UUID) -> TokenResponse:
    settings = get_settings()
    return TokenResponse(
        access_token=create_access_token(user_id),
        refresh_token=create_refresh_token(user_id, session_id),
        access_expires_in_seconds=settings.auth_access_token_lifetime_seconds,
        refresh_expires_in_seconds=settings.auth_refresh_token_lifetime_seconds,
    )


@router.post("/login", response_model=TokenResponse)
def login(request: LoginRequest, session: Session = Depends(get_db_session)) -> TokenResponse:
    user = session.scalar(select(User).where(User.email == request.email.strip().lower()))
    if user is None or not user.is_active or not verify_password(request.password, user.password_hash):
        record_audit(
            session,
            action="auth.login_failure",
            target_type="user",
            metadata={"identity_hash": _identity_hash(request.email)},
        )
        session.commit()
        raise _invalid_credentials()

    session_id = uuid.uuid4()
    response = _token_response(user.id, session_id)
    session.add(
        AuthSession(
            id=session_id,
            user_id=user.id,
            token_digest=token_digest(response.refresh_token),
            expires_at=utc_now() + timedelta(seconds=get_settings().auth_refresh_token_lifetime_seconds),
        )
    )
    record_audit(session, "auth.login_success", "user", user.id, actor_id=user.id)
    session.commit()
    return response


@router.post("/refresh", response_model=TokenResponse)
def refresh(request: RefreshRequest, session: Session = Depends(get_db_session)) -> TokenResponse:
    claims = decode_token(request.refresh_token, "refresh")
    try:
        user_id = uuid.UUID(str(claims["sub"]))
        session_id = uuid.UUID(str(claims["sid"]))
    except ValueError as error:
        raise _invalid_credentials() from error

    auth_session = session.get(AuthSession, session_id)
    user = session.get(User, user_id)
    if (
        auth_session is None
        or user is None
        or not user.is_active
        or auth_session.user_id != user.id
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= utc_now()
        or not hmac.compare_digest(auth_session.token_digest, token_digest(request.refresh_token))
    ):
        record_audit(session, "auth.refresh_failure", "auth_session")
        session.commit()
        raise _invalid_credentials()

    auth_session.revoked_at = utc_now()
    new_session_id = uuid.uuid4()
    response = _token_response(user.id, new_session_id)
    session.add(
        AuthSession(
            id=new_session_id,
            user_id=user.id,
            token_digest=token_digest(response.refresh_token),
            expires_at=utc_now() + timedelta(seconds=get_settings().auth_refresh_token_lifetime_seconds),
        )
    )
    record_audit(session, "auth.refresh_rotated", "auth_session", new_session_id, actor_id=user.id)
    session.commit()
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: LogoutRequest, session: Session = Depends(get_db_session)) -> None:
    claims = decode_token(request.refresh_token, "refresh")
    try:
        session_id = uuid.UUID(str(claims["sid"]))
        user_id = uuid.UUID(str(claims["sub"]))
    except ValueError as error:
        raise _invalid_credentials() from error
    auth_session = session.get(AuthSession, session_id)
    if auth_session is None or auth_session.user_id != user_id:
        raise _invalid_credentials()
    if auth_session.revoked_at is None:
        auth_session.revoked_at = utc_now()
        record_audit(session, "auth.logout", "auth_session", auth_session.id, actor_id=user_id)
        session.commit()


users_router = APIRouter(prefix="/users", tags=["users"])


@users_router.get("/me", response_model=CurrentUserResponse)
def get_current_user_profile(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_db_session),
) -> CurrentUserResponse:
    memberships = list(
        session.scalars(select(ProjectMember).where(ProjectMember.user_id == user.id))
    )
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        roles=sorted(user_roles(session, user.id)),
        permissions=sorted(user_permissions(session, user.id)),
        project_memberships=[
            ProjectMembershipResponse(project_id=membership.project_id, role=membership.role)
            for membership in memberships
        ],
    )

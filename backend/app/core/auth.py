"""Password, JWT, session, and authorization primitives for Phase B.3."""

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db_session
from app.models import AuthSession, Permission, ProjectMember, Role, RolePermission, User, UserRole

password_hasher = PasswordHasher()
bearer_scheme = HTTPBearer(auto_error=False)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _require_signing_secret() -> str:
    secret = get_settings().auth_jwt_secret
    if not secret or secret.startswith("replace_with_"):
        raise RuntimeError("AUTH_JWT_SECRET must be configured with a non-placeholder value.")
    return secret


def _encode_token(subject: uuid.UUID, token_type: str, expires_in_seconds: int, session_id: uuid.UUID | None = None) -> str:
    now = utc_now()
    claims: dict[str, object] = {
        "sub": str(subject),
        "typ": token_type,
        "iat": now,
        "exp": now + timedelta(seconds=expires_in_seconds),
        "jti": str(uuid.uuid4()),
    }
    if session_id is not None:
        claims["sid"] = str(session_id)
    return jwt.encode(claims, _require_signing_secret(), algorithm=get_settings().auth_jwt_algorithm)


def create_access_token(user_id: uuid.UUID, session_id: uuid.UUID | None = None) -> str:
    return _encode_token(
        user_id,
        "access",
        get_settings().auth_access_token_lifetime_seconds,
        session_id,
    )


def create_refresh_token(user_id: uuid.UUID, session_id: uuid.UUID) -> str:
    return _encode_token(
        user_id,
        "refresh",
        get_settings().auth_refresh_token_lifetime_seconds,
        session_id,
    )


def decode_token(token: str, expected_type: str) -> dict[str, object]:
    try:
        claims = jwt.decode(
            token,
            _require_signing_secret(),
            algorithms=[get_settings().auth_jwt_algorithm],
            options={"require": ["sub", "typ", "iat", "exp", "jti"]},
        )
    except InvalidTokenError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.") from error
    if claims.get("typ") != expected_type:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")
    if expected_type == "refresh" and not claims.get("sid"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")
    return claims


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    session: Annotated[Session, Depends(get_db_session)],
) -> User:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required.")
    claims = decode_token(credentials.credentials, "access")
    try:
        user_id = uuid.UUID(str(claims["sub"]))
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.") from error
    user = session.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")

    # Access tokens issued by the login/refresh API carry the refresh-session ID.
    # This makes logout and refresh rotation revoke the associated access token
    # immediately instead of waiting for its short JWT expiry.
    session_claim = claims.get("sid")
    if session_claim is not None:
        try:
            auth_session_id = uuid.UUID(str(session_claim))
        except ValueError as error:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.") from error
        auth_session = session.get(AuthSession, auth_session_id)
        if (
            auth_session is None
            or auth_session.user_id != user.id
            or auth_session.revoked_at is not None
            or auth_session.expires_at <= utc_now()
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid authentication token.")
    return user


def user_permissions(session: Session, user_id: uuid.UUID) -> set[str]:
    return set(
        session.scalars(
            select(Permission.code)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(Role, Role.id == RolePermission.role_id)
            .join(UserRole, UserRole.role_id == Role.id)
            .where(UserRole.user_id == user_id)
        )
    )


def user_roles(session: Session, user_id: uuid.UUID) -> list[str]:
    return list(
        session.scalars(
            select(Role.name).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id)
        )
    )


def require_permission(permission: str):
    def dependency(
        user: Annotated[User, Depends(get_current_user)],
        session: Annotated[Session, Depends(get_db_session)],
    ) -> User:
        if permission not in user_permissions(session, user.id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied.")
        return user

    return dependency


def require_project_access(session: Session, user: User, project_id: uuid.UUID) -> None:
    membership = session.get(ProjectMember, {"project_id": project_id, "user_id": user.id})
    if membership is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project access denied.")


def require_project_permission(session: Session, user: User, project_id: uuid.UUID, permission: str) -> None:
    if permission not in user_permissions(session, user.id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied.")
    require_project_access(session, user, project_id)

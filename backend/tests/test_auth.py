import uuid
from datetime import timedelta

import jwt
import pytest
from fastapi import HTTPException

from app.core.auth import create_access_token, create_refresh_token, decode_token, hash_password, utc_now, verify_password
from app.core.config import Settings
from app.core.permissions import PERMISSION_CODES, ROLE_PERMISSION_CODES


@pytest.fixture(autouse=True)
def test_auth_settings(monkeypatch):
    monkeypatch.setattr(
        "app.core.auth.get_settings",
        lambda: Settings(
            auth_jwt_secret="unit-test-secret-that-is-long-enough",
            auth_access_token_lifetime_seconds=60,
            auth_refresh_token_lifetime_seconds=300,
        ),
    )


def test_argon2id_hash_never_stores_plaintext() -> None:
    password_hash = hash_password("correct horse battery staple")
    assert password_hash != "correct horse battery staple"
    assert password_hash.startswith("$argon2id$")
    assert verify_password("correct horse battery staple", password_hash) is True
    assert verify_password("wrong password", password_hash) is False


def test_access_and_refresh_tokens_cannot_be_used_interchangeably() -> None:
    user_id = uuid.uuid4()
    access_token = create_access_token(user_id)
    refresh_token = create_refresh_token(user_id, uuid.uuid4())

    assert decode_token(access_token, "access")["sub"] == str(user_id)
    assert decode_token(refresh_token, "refresh")["sub"] == str(user_id)
    with pytest.raises(HTTPException) as error:
        decode_token(refresh_token, "access")
    assert error.value.status_code == 401


def test_expired_access_token_is_rejected() -> None:
    now = utc_now()
    expired_token = jwt.encode(
        {
            "sub": str(uuid.uuid4()),
            "typ": "access",
            "jti": str(uuid.uuid4()),
            "iat": now - timedelta(minutes=2),
            "exp": now - timedelta(minutes=1),
        },
        "unit-test-secret-that-is-long-enough",
        algorithm="HS256",
    )
    with pytest.raises(HTTPException) as error:
        decode_token(expired_token, "access")
    assert error.value.status_code == 401


def test_role_permission_mapping_matches_the_approved_baseline() -> None:
    assert ROLE_PERMISSION_CODES["ADMIN"] == frozenset(PERMISSION_CODES)
    assert "project:member_manage" not in ROLE_PERMISSION_CODES["OFFICER"]
    assert "review:act" in ROLE_PERMISSION_CODES["REVIEWER"]
    assert "geo:edit_draft" in ROLE_PERMISSION_CODES["SURVEYOR"]
    assert ROLE_PERMISSION_CODES["VIEWER"] == {
        "project:read", "document:read", "record:read", "geo:read", "dashboard:read", "export:read",
    }


def test_h1_role_permission_matrix_has_no_privilege_drift() -> None:
    expected = {
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
    assert ROLE_PERMISSION_CODES == expected


def test_access_token_can_be_bound_to_auth_session() -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    claims = decode_token(create_access_token(user_id, session_id), "access")
    assert claims["sub"] == str(user_id)
    assert claims["sid"] == str(session_id)

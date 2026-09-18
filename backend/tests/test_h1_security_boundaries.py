"""Phase H.1 focused security-boundary regression checks."""

import pytest
from fastapi.testclient import TestClient

from app.audit.service import sanitize_audit_metadata
from app.core.config import Settings
from app.main import app, settings as app_settings


def test_browser_cors_allows_configured_local_frontend_and_denies_unknown_origin() -> None:
    client = TestClient(app)
    assert app_settings.cors_origins
    allowed_origin = app_settings.cors_origins[0]
    allowed = client.options(
        "/api/v1/projects",
        headers={
            "Origin": allowed_origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == allowed_origin
    assert "authorization" in allowed.headers["access-control-allow-headers"].lower()
    assert allowed.headers.get("access-control-allow-credentials") != "true"

    denied = client.options(
        "/api/v1/projects",
        headers={
            "Origin": "https://untrusted.example.invalid",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_cors_configuration_rejects_wildcard_origin() -> None:
    settings = Settings(cors_allowed_origins="*")
    with pytest.raises(ValueError, match="wildcard"):
        _ = settings.cors_origins


def test_audit_sanitizer_redacts_nested_security_material() -> None:
    sanitized = sanitize_audit_metadata(
        {
            "safe": "visible",
            "access_token": "secret-token",
            "nested": {
                "authorization": "Bearer secret",
                "details": [
                    {"signed_url": "https://example.invalid/?X-Amz-Signature=secret"},
                    {"url": "https://example.invalid/?X-Amz-Signature=secret"},
                    {"note": "safe"},
                ],
            },
        }
    )
    assert sanitized["safe"] == "visible"
    assert "access_token" not in sanitized
    assert "authorization" not in sanitized["nested"]
    assert "signed_url" not in sanitized["nested"]["details"][0]
    assert sanitized["nested"]["details"][1]["url"] == "[redacted]"
    assert sanitized["nested"]["details"][2]["note"] == "safe"

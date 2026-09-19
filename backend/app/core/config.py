"""Environment-backed application configuration."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    """Settings loaded from environment variables without committing secrets."""

    model_config = SettingsConfigDict(case_sensitive=False, extra="ignore")

    app_env: str = "development"
    db_host: str = "postgres"
    db_port: int = Field(default=5432, ge=1, le=65535)
    db_name: str = "land_records"
    db_user: str = "land_records"
    db_password: str = ""
    redis_url: str = "redis://redis:6379/0"
    s3_endpoint: str = "http://minio:9000"
    s3_public_endpoint: str | None = None
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "land-records"
    s3_region: str = "us-east-1"
    signed_url_expiry_seconds: int = Field(default=900, ge=60, le=3600)
    auth_jwt_secret: str = ""
    auth_jwt_algorithm: str = "HS256"
    auth_access_token_lifetime_seconds: int = Field(default=900, ge=60, le=3600)
    auth_refresh_token_lifetime_seconds: int = Field(default=604800, ge=300, le=2592000)
    cors_allowed_origins: str = "http://localhost:5173"
    bootstrap_admin_email: str | None = None
    bootstrap_admin_password: str | None = None
    bootstrap_admin_full_name: str = "Development Administrator"
    bootstrap_admin_login_id: str | None = None
    geoai_building_checkpoint: str | None = None
    geoai_device: str = "auto"

    @property
    def cors_origins(self) -> list[str]:
        """Return explicit browser origins; wildcard origins are never accepted."""
        origins = [value.strip().rstrip("/") for value in self.cors_allowed_origins.split(",") if value.strip()]
        if any(origin == "*" for origin in origins):
            raise ValueError("CORS_ALLOWED_ORIGINS must list explicit origins; wildcard is not allowed.")
        return origins

    @property
    def database_url(self) -> str:
        """Build a safely escaped SQLAlchemy PostgreSQL URL."""
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password,
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()

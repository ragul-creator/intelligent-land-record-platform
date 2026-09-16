from fastapi import FastAPI, HTTPException, status
from botocore.exceptions import BotoCoreError, ClientError
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import engine
from app.core.config import get_settings
from app.core.storage import get_storage_service
from app.api.v1.files import router as files_router
from app.api.v1.auth import router as auth_router, users_router

app = FastAPI(
    title="Intelligent Land Record Platform API",
    version="0.1.0",
    description="Backend integration boundary. Public API contracts begin under /api/v1.",
)
app.include_router(files_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")


@app.get("/health", tags=["operations"])
async def health_check() -> dict[str, str]:
    """Liveness check that does not expose infrastructure details."""
    return {"status": "ok"}


@app.get("/ready", tags=["operations"])
async def readiness_check() -> dict[str, str]:
    """Readiness check for PostgreSQL/PostGIS, Redis, and private object storage."""
    try:
        with engine.connect() as connection:
            postgis_version = connection.execute(text("SELECT PostGIS_Version()")).scalar_one()
        Redis.from_url(get_settings().redis_url).ping()
        get_storage_service().ensure_private_bucket()
    except (SQLAlchemyError, RedisError, BotoCoreError, ClientError) as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A required backend dependency is not ready.",
        ) from error

    return {"status": "ready", "postgis_version": str(postgis_version)}

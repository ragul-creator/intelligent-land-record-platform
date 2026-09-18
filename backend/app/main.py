from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
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
from app.api.v1.jobs import project_router as project_jobs_router, router as jobs_router
from app.api.v1.projects import router as projects_router
from app.api.v1.geoai import router as geoai_router
from app.api.v1.review import router as review_router
from app.api.v1.documents import router as documents_router
from app.core.errors import ApiError

app = FastAPI(
    title="Intelligent Land Record Platform API",
    version="0.1.0",
    description="Backend integration boundary. Public API contracts begin under /api/v1.",
)
app.include_router(files_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(projects_router, prefix="/api/v1")
app.include_router(geoai_router, prefix="/api/v1")
app.include_router(review_router, prefix="/api/v1")
app.include_router(documents_router, prefix="/api/v1")
app.include_router(project_jobs_router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, error: ApiError) -> JSONResponse:
    return _error_response(error.status_code, error.code, str(error.detail))


@app.exception_handler(HTTPException)
async def http_error_handler(_: Request, error: HTTPException) -> JSONResponse:
    codes = {
        status.HTTP_401_UNAUTHORIZED: "AUTHENTICATION_FAILED",
        status.HTTP_403_FORBIDDEN: "FORBIDDEN",
        status.HTTP_404_NOT_FOUND: "NOT_FOUND",
        status.HTTP_409_CONFLICT: "CONFLICT",
        status.HTTP_422_UNPROCESSABLE_CONTENT: "VALIDATION_ERROR",
    }
    code = codes.get(error.status_code, "REQUEST_FAILED")
    return _error_response(error.status_code, code, str(error.detail))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, __: RequestValidationError) -> JSONResponse:
    # Preserve FastAPI's 422 semantic without echoing rejected values such as passwords.
    return _error_response(status.HTTP_422_UNPROCESSABLE_CONTENT, "VALIDATION_ERROR", "Request validation failed.")


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, __: Exception) -> JSONResponse:
    return _error_response(status.HTTP_500_INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "An internal error occurred.")


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

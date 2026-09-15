from fastapi import FastAPI

app = FastAPI(
    title="Intelligent Land Record Platform API",
    version="0.1.0",
    description="Phase A backend foundation. Public API contracts begin under /api/v1.",
)


@app.get("/health", tags=["operations"])
async def health_check() -> dict[str, str]:
    """Liveness check that does not expose infrastructure details."""
    return {"status": "ok"}

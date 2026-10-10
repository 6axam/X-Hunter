from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import text

from app.api.dependencies import DatabaseSession
from app.config import Settings, get_settings

router = APIRouter(tags=["system"])


class HealthResponse(BaseModel):
    status: str
    environment: str
    source_http_enabled: bool


@router.get("/health", response_model=HealthResponse)
async def health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    return HealthResponse(
        status="ok",
        environment=settings.app_env,
        source_http_enabled=settings.source_http_enabled,
    )


@router.get("/ready")
async def ready(
    db: DatabaseSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> JSONResponse:
    components = {"database": False, "redis": False, "source": settings.source_http_enabled}
    try:
        await db.execute(text("SELECT 1"))
        components["database"] = True
    except Exception:
        pass
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        components["redis"] = bool(await redis.ping())
    except Exception:
        pass
    finally:
        await redis.aclose()
    ready_state = components["database"] and components["redis"]
    return JSONResponse(
        {"status": "ready" if ready_state else "not_ready", "components": components},
        status_code=200 if ready_state else 503,
    )

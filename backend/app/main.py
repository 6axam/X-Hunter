from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import AppError, app_error_handler
from app.api.routes.auth import router as auth_router
from app.api.routes.core import router as core_router
from app.api.routes.health import router as health_router
from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title="OLX Hunter API",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Content-Type",
        "X-CSRF-Token",
        "Idempotency-Key",
        "X-Dev-Telegram-Id",
        "X-Request-ID",
    ],
)
app.add_exception_handler(AppError, app_error_handler)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or str(uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response


app.include_router(health_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1")
app.include_router(core_router, prefix="/api/v1")

from __future__ import annotations

from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field


class ApiError(BaseModel):
    code: str
    message: str
    field_errors: dict[str, list[str]] = Field(default_factory=dict)
    retry_after: int | None = None
    request_id: str


class AppError(Exception):
    def __init__(
        self,
        *,
        status_code: int,
        code: str,
        message: str,
        field_errors: dict[str, list[str]] | None = None,
        retry_after: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field_errors = field_errors or {}
        self.retry_after = retry_after


async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", str(uuid4()))
    payload = ApiError(
        code=exc.code,
        message=exc.message,
        field_errors=exc.field_errors,
        retry_after=exc.retry_after,
        request_id=request_id,
    )
    headers = {"X-Request-ID": request_id}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(payload.model_dump(), status_code=exc.status_code, headers=headers)

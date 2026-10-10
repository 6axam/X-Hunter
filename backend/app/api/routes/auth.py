from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select

from app.api.dependencies import DatabaseSession
from app.api.errors import AppError
from app.api.schemas import TelegramAuthRequest, TelegramAuthResponse
from app.config import Settings, get_settings
from app.models import User
from app.services.auth import SessionCodec, validate_telegram_init_data

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/telegram", response_model=TelegramAuthResponse)
async def telegram_auth(
    body: TelegramAuthRequest,
    response: Response,
    db: DatabaseSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> TelegramAuthResponse:
    if not settings.telegram_bot_token or settings.owner_telegram_id is None:
        raise AppError(
            status_code=503,
            code="telegram_not_configured",
            message="Telegram-авторизация ещё не настроена.",
        )
    identity = validate_telegram_init_data(
        body.init_data,
        settings.telegram_bot_token,
        max_age_seconds=settings.telegram_init_data_max_age_seconds,
    )
    if identity.telegram_id != settings.owner_telegram_id:
        raise AppError(status_code=403, code="access_denied", message="Доступ запрещён.")
    user = await db.scalar(select(User).where(User.telegram_id == identity.telegram_id))
    if user is None:
        user = User(telegram_id=identity.telegram_id, timezone=settings.default_timezone)
        db.add(user)
        await db.commit()
    token, csrf = SessionCodec(settings.session_secret).create(identity.telegram_id)
    response.set_cookie(
        "olx_hunter_session",
        token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="strict",
        path="/",
    )
    return TelegramAuthResponse(csrf_token=csrf, expires_in=settings.session_max_age_seconds)

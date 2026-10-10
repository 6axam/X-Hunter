from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.config import Settings, get_settings
from app.database import get_db
from app.models import User
from app.services.auth import SessionCodec

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    request: Request,
    db: DatabaseSession,
    settings: AppSettings,
) -> User:
    session_token = request.cookies.get("olx_hunter_session")
    if session_token:
        session = SessionCodec(settings.session_secret).read(
            session_token, settings.session_max_age_seconds
        )
        telegram_id = int(session["telegram_id"])
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            csrf = request.headers.get("X-CSRF-Token")
            if not csrf or not secrets.compare_digest(csrf, str(session["csrf"])):
                raise AppError(
                    status_code=403,
                    code="csrf_failed",
                    message="Защитный токен неверен.",
                )
    elif settings.dev_auth_enabled:
        requested_id = request.headers.get("X-Dev-Telegram-Id")
        telegram_id = settings.owner_telegram_id
        if requested_id:
            try:
                requested = int(requested_id)
            except ValueError as exc:
                raise AppError(
                    status_code=401,
                    code="invalid_dev_identity",
                    message="Некорректный development ID.",
                ) from exc
            if telegram_id is not None and requested != telegram_id:
                raise AppError(status_code=403, code="access_denied", message="Доступ запрещён.")
            telegram_id = requested
        if telegram_id is None:
            telegram_id = 1
    else:
        raise AppError(
            status_code=401,
            code="authentication_required",
            message="Авторизация Telegram ещё не выполнена.",
        )

    if settings.owner_telegram_id is None or telegram_id != settings.owner_telegram_id:
        raise AppError(status_code=403, code="access_denied", message="Доступ запрещён.")

    user = await db.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        if not settings.dev_auth_enabled:
            raise AppError(
                status_code=401,
                code="authentication_required",
                message="Сессия не связана с владельцем.",
            )
        user = User(telegram_id=telegram_id, timezone=settings.default_timezone)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    if not user.is_active:
        raise AppError(status_code=403, code="access_denied", message="Доступ запрещён.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

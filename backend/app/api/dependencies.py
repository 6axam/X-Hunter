from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.config import Settings, get_settings
from app.database import get_db
from app.models import User

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


async def get_current_user(
    request: Request,
    db: DatabaseSession,
    settings: AppSettings,
) -> User:
    if not settings.dev_auth_enabled:
        raise AppError(
            status_code=401,
            code="authentication_required",
            message="Авторизация Telegram ещё не выполнена.",
        )
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

    user = await db.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        user = User(telegram_id=telegram_id, timezone=settings.default_timezone)
        db.add(user)
        await db.commit()
        await db.refresh(user)
    if not user.is_active:
        raise AppError(status_code=403, code="access_denied", message="Доступ запрещён.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]

from __future__ import annotations

import asyncio
from urllib.parse import urlparse

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo
from sqlalchemy import select

from app.config import get_settings
from app.database import session_factory
from app.logging import configure_logging, get_logger
from app.models import User
from app.services.searches import SearchService
from app.sources.olx import OlxSource

router = Router()
logger = get_logger(__name__)


def app_url() -> str:
    return get_settings().public_base_url.rstrip("/")


def app_keyboard() -> InlineKeyboardMarkup | None:
    settings = get_settings()
    parsed = urlparse(settings.public_base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Открыть OLX Hunter",
                    web_app=WebAppInfo(url=settings.public_base_url),
                )
            ]
        ]
    )


def is_owner(message: Message) -> bool:
    owner_id = get_settings().owner_telegram_id
    return bool(message.from_user and owner_id and message.from_user.id == owner_id)


@router.message(Command("start", "app"))
async def open_app(message: Message) -> None:
    if not is_owner(message):
        await message.answer("Доступ к этому боту закрыт.")
        return
    keyboard = app_keyboard()
    text = "OLX Hunter следит за вашими поисками и показывает новые совпадения."
    if keyboard is None:
        text += (
            "\n\nЛокальный интерфейс доступен по адресу "
            f"{app_url()}, но Telegram Mini App требует публичный HTTPS-адрес."
        )
    await message.answer(text, reply_markup=keyboard)


@router.message(Command("help"))
async def help_message(message: Message) -> None:
    if not is_owner(message):
        return
    keyboard = app_keyboard()
    text = (
        "Отправьте ссылку на выдачу OLX, чтобы создать черновик. "
        "Настройка фильтров и управление поисками доступны в приложении."
    )
    if keyboard is None:
        text += f"\n\nЛокальный интерфейс: {app_url()}"
    await message.answer(text, reply_markup=keyboard)


@router.message(F.text.regexp(r"https?://(?:www\.)?olx\.ua/"))
async def import_link(message: Message) -> None:
    if not is_owner(message) or not message.text or not message.from_user:
        return
    settings = get_settings()
    async with session_factory() as db:
        owner = await db.scalar(select(User).where(User.telegram_id == message.from_user.id))
        if owner is None:
            owner = User(telegram_id=message.from_user.id, timezone=settings.default_timezone)
            db.add(owner)
            await db.commit()
            await db.refresh(owner)
        draft = await SearchService(
            db,
            OlxSource(
                http_enabled=settings.source_http_enabled,
                browser_enabled=settings.source_browser_enabled,
            ),
        ).import_url(owner, message.text.strip())
    keyboard = None
    if app_keyboard() is not None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="Настроить поиск",
                        web_app=WebAppInfo(url=f"{app_url()}/searches?draft={draft.id}"),
                    )
                ]
            ]
        )
    text = f"Распознал: {draft.name}"
    if keyboard is None:
        text += f"\n\nЛокальный интерфейс: {app_url()}/searches?draft={draft.id}"
    await message.answer(text, reply_markup=keyboard)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings, "bot")
    if not settings.telegram_bot_token or settings.owner_telegram_id is None:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and OWNER_TELEGRAM_ID are required")
    bot = Bot(settings.telegram_bot_token)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    logger.info("Telegram bot polling started", extra={"event": "bot_polling_started"})
    await dispatcher.start_polling(bot, allowed_updates=dispatcher.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())

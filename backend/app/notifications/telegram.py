from __future__ import annotations

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import DeliveryAttempt, NotificationEvent, User


class TelegramOutbox:
    def __init__(self, db: AsyncSession, settings: Settings, bot: Bot) -> None:
        self.db = db
        self.settings = settings
        self.bot = bot

    async def deliver_pending(self, limit: int = 20) -> int:
        events = list(
            await self.db.scalars(
                select(NotificationEvent)
                .where(NotificationEvent.status.in_(["pending", "retry"]))
                .order_by(NotificationEvent.created_at)
                .limit(limit)
            )
        )
        delivered = 0
        for event in events:
            owner = await self.db.get(User, event.owner_id)
            if owner is None or not owner.is_active:
                continue
            if await self.deliver(event, owner.telegram_id):
                delivered += 1
        return delivered

    async def deliver(self, event: NotificationEvent, telegram_id: int) -> bool:
        payload = event.payload
        title = str(payload.get("title") or "Объявление")
        price = payload.get("price")
        currency = "грн" if payload.get("currency") == "UAH" else payload.get("currency") or ""
        city = payload.get("city") or "Город не указан"
        marker = "Цена снизилась" if event.event_type == "price_drop" else "Новое совпадение"
        caption = f"{marker}\n\n{title}\n{price or 'Цена не указана'} {currency}\n{city}"[:1024]
        url = str(payload.get("url") or "")
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Открыть в OLX", url=url),
                    InlineKeyboardButton(
                        text="Подробнее",
                        web_app=WebAppInfo(
                            url=f"{self.settings.public_base_url}/findings/{event.listing_id}"
                        ),
                    ),
                ]
            ]
        )
        attempt = DeliveryAttempt(
            event_id=event.id,
            channel="telegram",
            destination=str(telegram_id),
            status="sending",
        )
        self.db.add(attempt)
        await self.db.commit()
        try:
            message = await self.bot.send_message(
                telegram_id,
                caption,
                reply_markup=keyboard,
                disable_web_page_preview=False,
            )
        except TelegramRetryAfter as exc:
            attempt.status = "retry"
            attempt.retry_after = int(exc.retry_after)
            attempt.error_code = "telegram_rate_limit"
            event.status = "retry"
        except TelegramForbiddenError:
            attempt.status = "failed"
            attempt.error_code = "bot_blocked"
            event.status = "blocked"
        except Exception:
            # Telegram may have accepted the request before a network response was lost.
            attempt.status = "delivery_unknown"
            attempt.uncertain_delivery = True
            attempt.error_code = "telegram_response_unknown"
            event.status = "delivery_unknown"
        else:
            attempt.status = "sent"
            attempt.message_id = str(message.message_id)
            event.status = "sent"
            await self.db.commit()
            return True
        await self.db.commit()
        return False

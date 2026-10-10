from __future__ import annotations

import asyncio
from datetime import timedelta
from uuid import UUID

from aiogram import Bot
from sqlalchemy import select

from app.config import get_settings
from app.database import session_factory
from app.jobs.celery_app import celery_app
from app.models import Search
from app.models.base import utcnow
from app.notifications.telegram import TelegramOutbox
from app.services.media import process_pending_media
from app.services.runs import RunService
from app.sources.olx import OlxSource


@celery_app.task(name="app.jobs.tasks.schedule_due_searches")
def schedule_due_searches() -> int:
    return asyncio.run(_schedule_due_searches())


async def _schedule_due_searches() -> int:
    async with session_factory() as db:
        due = list(
            await db.scalars(
                select(Search).where(
                    Search.active.is_(True),
                    Search.next_run_at.is_not(None),
                    Search.next_run_at <= utcnow(),
                )
            )
        )
        for search in due:
            search.next_run_at = utcnow() + timedelta(minutes=search.interval_minutes)
            run_search.delay(str(search.id))
        await db.commit()
        return len(due)


@celery_app.task(name="app.jobs.tasks.run_search")
def run_search(search_id: str) -> str:
    return asyncio.run(_run_search(UUID(search_id)))


async def _run_search(search_id: UUID) -> str:
    settings = get_settings()
    async with session_factory() as db:
        search = await db.get(Search, search_id)
        if search is None or not search.active:
            return "skipped"
        run = await RunService(db, OlxSource(http_enabled=settings.source_http_enabled)).start(
            search
        )
        return run.status


@celery_app.task(name="app.jobs.tasks.deliver_notifications")
def deliver_notifications() -> int:
    return asyncio.run(_deliver_notifications())


async def _deliver_notifications() -> int:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return 0
    async with Bot(settings.telegram_bot_token) as bot, session_factory() as db:
        return await TelegramOutbox(db, settings, bot).deliver_pending()


@celery_app.task(name="app.jobs.tasks.download_media")
def download_media() -> int:
    return asyncio.run(_download_media())


async def _download_media() -> int:
    settings = get_settings()
    async with session_factory() as db:
        return await process_pending_media(db, settings)

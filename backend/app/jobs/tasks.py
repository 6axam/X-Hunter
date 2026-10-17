from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from datetime import timedelta
from pathlib import Path
from uuid import UUID

from aiogram import Bot
from sqlalchemy import and_, or_, select

from app.ai.openrouter import OpenRouterClient
from app.config import get_settings
from app.database import engine, session_factory
from app.jobs.celery_app import celery_app
from app.logging import get_logger
from app.models import ExportJob, Listing, ListingVersion, ResearchSession, Search, SearchMatch
from app.models.base import utcnow
from app.notifications.telegram import TelegramOutbox
from app.services.ai import AiService
from app.services.media import process_pending_media
from app.services.research import ResearchService
from app.services.runs import RunService
from app.services.scanning import ScanService
from app.sources.olx import OlxSource

logger = get_logger(__name__)


def _run_task[Result](coroutine: Coroutine[None, None, Result]) -> Result:
    """Run a Celery coroutine without leaking asyncpg connections into the next loop.

    Celery tasks are synchronous entry points and each one uses ``asyncio.run``.  An asyncpg
    connection kept by SQLAlchemy's global pool belongs to the loop that created it, so it cannot
    safely be reused by the next task's new loop.  Dispose the pool before that loop closes.
    """

    async def runner() -> Result:
        try:
            return await coroutine
        finally:
            await engine.dispose()

    try:
        return asyncio.run(runner())
    except Exception:
        logger.exception("Background task crashed", extra={"event": "task_crashed"})
        raise


@celery_app.task(name="app.jobs.tasks.schedule_due_searches")
def schedule_due_searches() -> int:
    return _run_task(_schedule_due_searches())


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
    return _run_task(_run_search(UUID(search_id)))


async def _run_search(search_id: UUID) -> str:
    settings = get_settings()
    async with session_factory() as db:
        search = await db.get(Search, search_id)
        if search is None or not search.active:
            return "skipped"
        run = await RunService(
            db,
            OlxSource(
                http_enabled=settings.source_http_enabled,
                browser_enabled=settings.source_browser_enabled,
            ),
            max_pages=settings.scan_max_pages,
        ).start(search)
        return run.status


@celery_app.task(name="app.jobs.tasks.run_research")
def run_research(session_id: str) -> str:
    return _run_task(_run_research(UUID(session_id)))


async def _run_research(session_id: UUID) -> str:
    settings = get_settings()
    logger.info(
        "Research task started", extra={"research_id": session_id, "event": "research_task_started"}
    )
    async with session_factory() as db:
        status = await ResearchService(db, settings).run(session_id)
    logger.info(
        "Research task finished",
        extra={"research_id": session_id, "event": "research_task_finished", "status": status},
    )
    return status


@celery_app.task(name="app.jobs.tasks.schedule_research")
def schedule_research() -> int:
    return _run_task(_schedule_research())


async def _schedule_research() -> int:
    async with session_factory() as db:
        sessions = list(
            await db.scalars(
                select(ResearchSession)
                .where(
                    or_(
                        ResearchSession.status == "queued",
                        and_(
                            ResearchSession.status == "running",
                            ResearchSession.lease_expires_at.is_not(None),
                            ResearchSession.lease_expires_at < utcnow(),
                        ),
                    )
                )
                .limit(20)
            )
        )
        for session in sessions:
            run_research.delay(str(session.id))
        return len(sessions)


@celery_app.task(name="app.jobs.tasks.deliver_notifications")
def deliver_notifications() -> int:
    return _run_task(_deliver_notifications())


async def _deliver_notifications() -> int:
    settings = get_settings()
    if not settings.telegram_bot_token:
        return 0
    async with Bot(settings.telegram_bot_token) as bot, session_factory() as db:
        return await TelegramOutbox(db, settings, bot).deliver_pending()


@celery_app.task(name="app.jobs.tasks.download_media")
def download_media() -> int:
    return _run_task(_download_media())


async def _download_media() -> int:
    settings = get_settings()
    async with session_factory() as db:
        return await process_pending_media(db, settings)


@celery_app.task(name="app.jobs.tasks.evaluate_ai")
def evaluate_ai() -> int:
    return _run_task(_evaluate_ai())


async def _evaluate_ai() -> int:
    settings = get_settings()
    if not settings.openrouter_api_key:
        return 0
    client = OpenRouterClient(settings.openrouter_api_key)
    models = {item.get("id"): item for item in await client.list_models()}
    completed = 0
    async with session_factory() as db:
        searches = list(await db.scalars(select(Search).where(Search.active.is_(True))))
        service = AiService(db)
        for search in searches:
            if not search.ai_settings.get("enabled"):
                continue
            model = search.ai_settings.get("model")
            if model not in models:
                continue
            matches = list(
                await db.scalars(
                    select(SearchMatch).where(
                        SearchMatch.search_id == search.id,
                        SearchMatch.decision == "match",
                    )
                )
            )
            for match in matches[:10]:
                version = await db.get(ListingVersion, match.listing_version_id)
                if version is None:
                    continue
                try:
                    await service.evaluate_match(search, match, version, client, models[model])
                except Exception:
                    continue
                completed += 1
    return completed


@celery_app.task(name="app.jobs.tasks.check_availability")
def check_availability() -> int:
    return _run_task(_check_availability())


async def _check_availability() -> int:
    settings = get_settings()
    if not settings.source_http_enabled:
        return 0
    now = utcnow()
    async with session_factory() as db:
        listings = list(
            await db.scalars(
                select(Listing)
                .where(
                    or_(
                        (Listing.unavailable_checks == 0)
                        & (Listing.last_seen_at <= now - timedelta(days=1)),
                        (Listing.unavailable_checks == 1)
                        & (Listing.updated_at <= now - timedelta(hours=6)),
                    )
                )
                .limit(50)
            )
        )
        source = OlxSource(http_enabled=True, browser_enabled=settings.source_browser_enabled)
        scanner = ScanService(db)
        for listing in listings:
            result = await source.check_availability(listing.canonical_url)
            await scanner.record_availability(
                listing,
                result.state,
                confirmed=bool(result.metadata.get("confirmed")),
            )
        return len(listings)


@celery_app.task(name="app.jobs.tasks.cleanup_exports")
def cleanup_exports() -> int:
    return _run_task(_cleanup_exports())


async def _cleanup_exports() -> int:
    async with session_factory() as db:
        jobs = list(
            await db.scalars(
                select(ExportJob).where(
                    ExportJob.expires_at.is_not(None),
                    ExportJob.expires_at <= utcnow(),
                )
            )
        )
        for job in jobs:
            if job.safe_path:
                await asyncio.to_thread(Path(job.safe_path).unlink, missing_ok=True)
            await db.delete(job)
        await db.commit()
        return len(jobs)

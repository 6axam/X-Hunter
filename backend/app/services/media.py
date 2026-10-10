from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.media.storage import MediaRejected, MediaStore
from app.models import Media, Search, SearchMatch, User


async def process_pending_media(db: AsyncSession, settings: Settings, limit: int = 10) -> int:
    pending = list(
        await db.scalars(
            select(Media).where(Media.status == "pending").order_by(Media.created_at).limit(limit)
        )
    )
    store = MediaStore(settings)
    completed = 0
    for media in pending:
        accepted = await db.scalar(
            select(SearchMatch.id).where(
                SearchMatch.listing_id == media.listing_id,
                SearchMatch.decision == "match",
            )
        )
        if not accepted:
            continue
        media.status = "downloading"
        await db.commit()
        try:
            body, _ = await store.download(media.source_url)
            values = store.store_bytes(body)
        except (MediaRejected, Exception) as exc:
            media.status = "rejected" if isinstance(exc, MediaRejected) else "failed"
        else:
            for key, value in values.items():
                setattr(media, key, value)
            media.status = "ready"
            completed += 1
        await db.commit()
    return completed


async def owner_can_access_media(db: AsyncSession, owner: User, media: Media) -> bool:
    return bool(
        await db.scalar(
            select(SearchMatch.id)
            .join(Search, Search.id == SearchMatch.search_id)
            .where(Search.owner_id == owner.id, SearchMatch.listing_id == media.listing_id)
        )
    )

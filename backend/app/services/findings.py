from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models import Listing, ListingVersion, Media, Search, SearchMatch, User, UserListingState


class FindingService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(self, owner: User, *, saved: bool | None, status: str | None) -> list[dict]:
        rows = (
            await self.db.execute(
                select(SearchMatch, Search, Listing, ListingVersion)
                .join(Search, Search.id == SearchMatch.search_id)
                .join(Listing, Listing.id == SearchMatch.listing_id)
                .join(ListingVersion, ListingVersion.id == SearchMatch.listing_version_id)
                .where(Search.owner_id == owner.id)
                .order_by(SearchMatch.last_seen_at.desc())
            )
        ).all()
        grouped: dict[UUID, dict] = {}
        for match, search, listing, version in rows:
            state = await self.db.scalar(
                select(UserListingState).where(
                    UserListingState.owner_id == owner.id,
                    UserListingState.listing_id == listing.id,
                )
            )
            if saved is not None and bool(state and state.saved) is not saved:
                continue
            if status and status != "all" and match.decision != status:
                continue
            if state and state.hidden and status != "hidden":
                continue
            item = grouped.get(listing.id)
            if item is None:
                cover = await self.db.scalar(
                    select(Media)
                    .where(
                        Media.listing_id == listing.id,
                        Media.status == "ready",
                        Media.safe_path.is_not(None),
                    )
                    .order_by(Media.position)
                )
                item = self._serialize(listing, version, match, state, cover)
                grouped[listing.id] = item
            item["searches"].append({"id": str(search.id), "name": search.name})
        return list(grouped.values())

    async def get(self, owner: User, listing_id: UUID) -> dict:
        items = await self.list(owner, saved=None, status="all")
        for item in items:
            if item["id"] == str(listing_id):
                media = list(
                    await self.db.scalars(
                        select(Media).where(Media.listing_id == listing_id).order_by(Media.position)
                    )
                )
                item["media"] = [
                    {"id": str(entry.id), "status": entry.status, "position": entry.position}
                    for entry in media
                ]
                return item
        raise AppError(status_code=404, code="finding_not_found", message="Находка не найдена.")

    async def patch_state(self, owner: User, listing_id: UUID, changes: dict) -> dict:
        await self.get(owner, listing_id)
        state = await self.db.scalar(
            select(UserListingState).where(
                UserListingState.owner_id == owner.id,
                UserListingState.listing_id == listing_id,
            )
        )
        if state is None:
            state = UserListingState(owner_id=owner.id, listing_id=listing_id)
            self.db.add(state)
        for key, value in changes.items():
            setattr(state, key, value)
        await self.db.commit()
        return {
            "saved": state.saved,
            "hidden": state.hidden,
            "seen": state.seen,
            "note": state.note,
        }

    @staticmethod
    def _serialize(listing, version, match, state, cover) -> dict:
        return {
            "id": str(listing.id),
            "title": version.title,
            "description": version.description,
            "price": str(version.price) if version.price is not None else None,
            "currency": version.currency,
            "city": version.city,
            "condition": version.condition,
            "canonical_url": listing.canonical_url,
            "decision": match.decision,
            "reasons": match.reasons,
            "first_seen_at": listing.first_seen_at,
            "last_seen_at": listing.last_seen_at,
            "saved": bool(state and state.saved),
            "hidden": bool(state and state.hidden),
            "seen": bool(state and state.seen),
            "note": state.note if state else None,
            "cover_media_id": str(cover.id) if cover else None,
            "searches": [],
        }

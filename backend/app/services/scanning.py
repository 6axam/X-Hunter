from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Listing,
    ListingVersion,
    Media,
    NotificationEvent,
    Search,
    SearchMatch,
)
from app.models.base import utcnow
from app.services.filtering import FilterDecision, evaluate
from app.sources.base import SourceState
from app.sources.types import ListingCandidate


def content_hash(candidate: ListingCandidate) -> str:
    payload = candidate.model_dump(mode="json", exclude={"image_urls", "promoted"})
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(encoded.encode()).hexdigest()


class ScanService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def ingest(self, search: Search, candidate: ListingCandidate) -> tuple[SearchMatch, bool]:
        listing, version, is_new_listing, previous_version = await self.upsert_listing(candidate)
        now = utcnow()
        decision: FilterDecision = evaluate(candidate, search.specification, search.rules)
        match = await self.db.scalar(
            select(SearchMatch).where(
                SearchMatch.search_id == search.id,
                SearchMatch.listing_id == listing.id,
            )
        )
        is_new_match = match is None
        if match is None:
            match = SearchMatch(
                search_id=search.id,
                listing_id=listing.id,
                search_revision=search.revision,
                listing_version_id=version.id,
                decision=decision.decision,
                reasons=decision.reasons,
                first_seen_at=now,
                last_seen_at=now,
            )
            self.db.add(match)
        else:
            match.search_revision = search.revision
            match.listing_version_id = version.id
            match.decision = decision.decision
            match.reasons = decision.reasons
            match.last_seen_at = now

        baseline = search.baseline_completed_at is None
        if decision.decision == "match" and is_new_match and not baseline:
            await self._event(
                search, listing, "new_listing", f"new:{search.owner_id}:{listing.id}", candidate
            )

        threshold = int(search.event_settings.get("price_drop_percent", 5))
        if (
            decision.decision == "match"
            and search.event_settings.get("price_drop_enabled", False)
            and previous_version is not None
            and previous_version.price is not None
            and candidate.price is not None
            and candidate.price < previous_version.price
        ):
            reference = await self._notified_price(search, listing) or previous_version.price
            percent = int((reference - candidate.price) * 100 / reference)
            if percent >= threshold:
                await self._event(
                    search,
                    listing,
                    "price_drop",
                    f"price:{search.owner_id}:{listing.id}:{candidate.price}",
                    candidate,
                    {"previous_price": str(reference), "drop_percent": percent},
                )
        await self.db.commit()
        return match, is_new_listing

    async def upsert_listing(
        self, candidate: ListingCandidate
    ) -> tuple[Listing, ListingVersion, bool, ListingVersion | None]:
        now = utcnow()
        listing = await self.db.scalar(
            select(Listing).where(
                Listing.source == "olx",
                Listing.market == "ua",
                Listing.source_listing_id == candidate.source_listing_id,
            )
        )
        is_new_listing = listing is None
        previous_version = None
        if listing is None:
            listing = Listing(
                source="olx",
                market="ua",
                source_listing_id=candidate.source_listing_id,
                canonical_url=candidate.canonical_url,
                first_seen_at=now,
                last_seen_at=now,
                unavailable_checks=0,
            )
            self.db.add(listing)
            await self.db.flush()
        else:
            if listing.current_version_id:
                previous_version = await self.db.get(ListingVersion, listing.current_version_id)
            listing.canonical_url = candidate.canonical_url
            listing.last_seen_at = now
            listing.unavailable_checks = 0

        digest = content_hash(candidate)
        version = await self.db.scalar(
            select(ListingVersion).where(
                ListingVersion.listing_id == listing.id,
                ListingVersion.content_hash == digest,
            )
        )
        if version is None:
            version = ListingVersion(
                listing_id=listing.id,
                content_hash=digest,
                title=candidate.title,
                description=candidate.description,
                price=candidate.price,
                currency=candidate.currency,
                raw_price=candidate.raw_price,
                price_type=candidate.price_type,
                city=candidate.city,
                condition=candidate.condition,
                attributes=candidate.attributes,
                published_at=candidate.published_at,
            )
            self.db.add(version)
            await self.db.flush()
            listing.current_version_id = version.id

        for position, source_url in enumerate(candidate.image_urls):
            existing_media = await self.db.scalar(
                select(Media).where(Media.listing_id == listing.id, Media.position == position)
            )
            if existing_media is None:
                self.db.add(
                    Media(
                        listing_id=listing.id,
                        source_url=source_url,
                        position=position,
                        status="pending",
                    )
                )

        await self.db.flush()
        return listing, version, is_new_listing, previous_version

    async def _notified_price(self, search: Search, listing: Listing) -> Decimal | None:
        event = await self.db.scalar(
            select(NotificationEvent)
            .where(
                NotificationEvent.owner_id == search.owner_id,
                NotificationEvent.listing_id == listing.id,
                NotificationEvent.status == "sent",
                NotificationEvent.event_type.in_(["new_listing", "price_drop"]),
            )
            .order_by(NotificationEvent.created_at.desc())
        )
        if not event or event.payload.get("price") is None:
            return None
        return Decimal(str(event.payload["price"]))

    async def complete_baseline(self, search: Search) -> None:
        if search.baseline_completed_at is None:
            search.baseline_completed_at = utcnow()
            await self.db.commit()

    async def _event(
        self,
        search: Search,
        listing: Listing,
        event_type: str,
        dedup_key: str,
        candidate: ListingCandidate,
        extra: dict | None = None,
    ) -> None:
        exists = await self.db.scalar(
            select(NotificationEvent.id).where(NotificationEvent.dedup_key == dedup_key)
        )
        if exists:
            return
        payload = {
            "title": candidate.title,
            "price": str(candidate.price) if candidate.price is not None else None,
            "currency": candidate.currency,
            "city": candidate.city,
            "url": candidate.canonical_url,
            **(extra or {}),
        }
        self.db.add(
            NotificationEvent(
                owner_id=search.owner_id,
                listing_id=listing.id,
                search_id=search.id,
                event_type=event_type,
                dedup_key=dedup_key,
                payload=payload,
                status="pending",
            )
        )

    async def record_availability(
        self, listing: Listing, state: SourceState, *, confirmed: bool
    ) -> bool:
        if state is SourceState.COMPLETE:
            listing.unavailable_checks = 0
            listing.last_seen_at = utcnow()
            await self.db.commit()
            return False
        if state is not SourceState.UNAVAILABLE or not confirmed:
            return False
        listing.unavailable_checks += 1
        if listing.unavailable_checks < 2:
            await self.db.commit()
            return False
        paths = list(
            await self.db.scalars(
                select(Media.safe_path).where(
                    Media.listing_id == listing.id,
                    Media.safe_path.is_not(None),
                )
            )
        )
        await self.db.execute(delete(Listing).where(Listing.id == listing.id))
        await self.db.commit()
        for path in paths:
            await asyncio.to_thread(Path(path).unlink, missing_ok=True)
        return True

from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Listing,
    ListingVersion,
    NotificationEvent,
    Search,
    SearchMatch,
    User,
    UserListingState,
)
from app.models.base import utcnow
from app.services.scanning import ScanService
from app.sources.base import SourceState
from app.sources.types import ListingCandidate


async def make_search(db: AsyncSession, owner: User, name: str = "Deck") -> Search:
    search = Search(
        owner_id=owner.id,
        name=name,
        original_url="https://www.olx.ua/uk/elektronika/",
        current_url="https://www.olx.ua/uk/elektronika/",
        specification={"price_to": "25000"},
        rules={"required_groups": [{"values": ["steam deck"], "scope": "both"}]},
        ai_settings={"enabled": False},
        event_settings={"price_drop_percent": 5},
        active=True,
        interval_minutes=15,
        next_run_at=utcnow(),
    )
    db.add(search)
    await db.commit()
    await db.refresh(search)
    return search


def candidate(price: str = "20000", listing_id: str = "IDdeck1") -> ListingCandidate:
    return ListingCandidate(
        source_listing_id=listing_id,
        canonical_url=f"https://www.olx.ua/d/uk/obyavlenie/deck-{listing_id}.html",
        title="Steam Deck OLED",
        description="Повний комплект",
        price=Decimal(price),
        currency="UAH",
        raw_price=f"{price} грн",
        price_type="fixed",
        image_urls=["https://img.example/one.jpg"],
    )


@pytest.mark.asyncio
async def test_baseline_deduplication_and_price_drop(db_session: AsyncSession) -> None:
    owner = User(telegram_id=111)
    db_session.add(owner)
    await db_session.commit()
    await db_session.refresh(owner)
    first = await make_search(db_session, owner, "First")
    second = await make_search(db_session, owner, "Second")
    scanner = ScanService(db_session)

    await scanner.ingest(first, candidate())
    await scanner.complete_baseline(first)
    await scanner.complete_baseline(second)
    await scanner.ingest(first, candidate(listing_id="IDnew"))
    await scanner.ingest(second, candidate(listing_id="IDnew"))

    events = list(await db_session.scalars(select(NotificationEvent)))
    assert [event.event_type for event in events] == ["new_listing"]
    assert await db_session.scalar(select(func.count()).select_from(Listing)) == 2

    await scanner.ingest(first, candidate(price="17000"))
    events = list(
        await db_session.scalars(select(NotificationEvent).order_by(NotificationEvent.created_at))
    )
    assert [event.event_type for event in events] == ["new_listing", "price_drop"]
    assert events[-1].payload["drop_percent"] == 15


@pytest.mark.asyncio
async def test_two_confirmed_unavailable_checks_delete_everything(db_session: AsyncSession) -> None:
    owner = User(telegram_id=111)
    db_session.add(owner)
    await db_session.commit()
    await db_session.refresh(owner)
    search = await make_search(db_session, owner)
    scanner = ScanService(db_session)
    await scanner.complete_baseline(search)
    await scanner.ingest(search, candidate())
    listing = await db_session.scalar(select(Listing))
    assert listing is not None
    db_session.add(UserListingState(owner_id=owner.id, listing_id=listing.id, saved=True))
    await db_session.commit()

    assert await scanner.record_availability(listing, SourceState.BLOCKED, confirmed=False) is False
    first_check = await scanner.record_availability(
        listing, SourceState.UNAVAILABLE, confirmed=True
    )
    second_check = await scanner.record_availability(
        listing, SourceState.UNAVAILABLE, confirmed=True
    )
    assert first_check is False
    assert second_check is True

    assert await db_session.scalar(select(func.count()).select_from(Listing)) == 0
    assert await db_session.scalar(select(func.count()).select_from(ListingVersion)) == 0
    assert await db_session.scalar(select(func.count()).select_from(SearchMatch)) == 0
    assert await db_session.scalar(select(func.count()).select_from(UserListingState)) == 0

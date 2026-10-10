from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.scanning import ScanService
from app.sources.types import ListingCandidate
from tests.test_scanning import make_search


@pytest.mark.asyncio
async def test_findings_feed_and_saved_state(
    api_client: httpx.AsyncClient, db_session: AsyncSession
) -> None:
    assert (await api_client.get("/api/v1/me")).status_code == 200
    owner = await db_session.scalar(select(User).where(User.telegram_id == 111))
    assert owner is not None
    search = await make_search(db_session, owner)
    await ScanService(db_session).ingest(
        search,
        ListingCandidate(
            source_listing_id="IDapi1",
            canonical_url="https://www.olx.ua/d/uk/obyavlenie/deck-IDapi1.html",
            title="Steam Deck OLED",
            description="Полный комплект",
            price=Decimal("19000"),
            currency="UAH",
        ),
    )

    feed = await api_client.get("/api/v1/findings")
    assert feed.status_code == 200, feed.text
    finding = feed.json()[0]
    assert finding["title"] == "Steam Deck OLED"
    assert finding["saved"] is False

    changed = await api_client.patch(
        f"/api/v1/findings/{finding['id']}/state", json={"saved": True, "seen": True}
    )
    assert changed.status_code == 200
    assert changed.json()["saved"] is True

    saved = await api_client.get("/api/v1/findings?saved=true")
    assert len(saved.json()) == 1


@pytest.mark.asyncio
async def test_manual_scan_reports_source_not_configured(api_client: httpx.AsyncClient) -> None:
    imported = await api_client.post(
        "/api/v1/search-imports",
        json={"url": "https://www.olx.ua/uk/elektronika/"},
    )
    draft = imported.json()
    created = await api_client.post(
        "/api/v1/searches",
        json={"draft_id": draft["id"], "active": True, "interval_minutes": 15},
    )
    scan = await api_client.post(f"/api/v1/searches/{created.json()['id']}/scans")
    assert scan.status_code == 200, scan.text
    assert scan.json()["status"] == "not_configured"
    assert scan.json()["error_code"] == "source_http_disabled"

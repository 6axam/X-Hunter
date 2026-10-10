from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from app.services.filtering import evaluate
from app.sources.base import SourceState
from app.sources.olx import OlxSource
from app.sources.parser import parse_listing_html, parse_search_html

FIXTURES = Path(__file__).parent / "fixtures"


def test_search_parser_ignores_banner_and_deduplicates_promoted_card() -> None:
    items = parse_search_html((FIXTURES / "search_page.html").read_text())
    assert [item.source_listing_id for item in items] == ["IDdeck1", "IDcase1"]
    assert items[0].price == Decimal("18500")
    assert items[0].promoted is False
    assert items[0].canonical_url.endswith("steam-deck-IDdeck1.html")


def test_listing_parser_keeps_full_description_and_images() -> None:
    item = parse_listing_html(
        (FIXTURES / "listing_page.html").read_text(),
        "https://www.olx.ua/d/uk/obyavlenie/steam-deck-IDdeck1.html?utm_source=test",
    )
    assert item.description == "Не был в ремонте. Полный комплект."
    assert item.price == Decimal("18500")
    assert item.image_urls == ["https://img.example/deck-1.jpg"]


def test_strict_filter_reads_description_without_semantic_negation() -> None:
    item = parse_listing_html(
        (FIXTURES / "listing_page.html").read_text(),
        "https://www.olx.ua/d/uk/obyavlenie/steam-deck-IDdeck1.html",
    )
    decision = evaluate(
        item,
        {"price_to": "20000"},
        {
            "required_groups": [{"values": ["полный комплект"], "scope": "description"}],
            "excluded": [{"value": "ремонт", "scope": "description"}],
        },
    )
    assert decision.decision == "reject"
    assert decision.reasons[0]["code"] == "excluded_phrase"


def test_filter_rejects_accessory_only_title() -> None:
    items = parse_search_html((FIXTURES / "search_page.html").read_text())
    decision = evaluate(
        items[1],
        {},
        {"required_groups": [], "excluded": [], "reject_accessories": True},
    )
    assert decision.decision == "reject"
    assert decision.reasons[0]["code"] == "accessory_only"


@pytest.mark.asyncio
async def test_http_source_fetches_fixture_documents_and_classifies_404() -> None:
    search_html = (FIXTURES / "search_page.html").read_text()
    listing_html = (FIXTURES / "listing_page.html").read_text()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("removed-IDgone.html"):
            return httpx.Response(404, headers={"content-type": "text/html"})
        if "/d/" in request.url.path:
            return httpx.Response(200, text=listing_html, headers={"content-type": "text/html"})
        return httpx.Response(200, text=search_html, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        source = OlxSource(http_enabled=True, client=client)
        parsed = source.parse_search_url("https://www.olx.ua/uk/elektronika/")
        assert parsed.search is not None
        page = await source.fetch_search_page(parsed.search)
        listing = await source.fetch_listing(page.data[0]["canonical_url"])
        removed = await source.check_availability(
            "https://www.olx.ua/d/uk/obyavlenie/removed-IDgone.html"
        )

    assert page.state is SourceState.COMPLETE and len(page.data) == 2
    assert listing.state is SourceState.COMPLETE
    assert listing.data["description"] == "Не был в ремонте. Полный комплект."
    assert removed.state is SourceState.UNAVAILABLE
    assert removed.metadata["confirmed"] is True

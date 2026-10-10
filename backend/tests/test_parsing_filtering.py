from decimal import Decimal
from pathlib import Path

from app.services.filtering import evaluate
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


def test_filter_reads_description_and_understands_simple_negation() -> None:
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
    assert decision.decision == "accepted"


def test_filter_rejects_accessory_only_title() -> None:
    items = parse_search_html((FIXTURES / "search_page.html").read_text())
    decision = evaluate(
        items[1],
        {},
        {"required_groups": [], "excluded": [], "reject_accessories": True},
    )
    assert decision.decision == "rejected"
    assert decision.reasons[0]["code"] == "accessory_only"

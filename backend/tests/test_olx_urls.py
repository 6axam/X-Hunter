from decimal import Decimal

import pytest

from app.api.errors import AppError
from app.sources.olx import OlxPageType, OlxSource


def test_search_url_round_trip_preserves_supported_and_unknown_filters() -> None:
    source = OlxSource()
    raw = (
        "https://www.olx.ua/uk/elektronika/?utm_source=test&page=4&currency=UAH"
        "&search%5Bprivate_business%5D=private"
        "&search%5Border%5D=created_at%3Adesc"
        "&search%5Bfilter_float_price%3Afrom%5D=1000"
        "&search%5Bfilter_float_price%3Ato%5D=10000"
        "&search%5Bfilter_enum_state%5D%5B0%5D=used"
    )

    parsed = source.parse_search_url(raw)

    assert parsed.page_type is OlxPageType.SEARCH
    assert parsed.search is not None
    assert parsed.search.price_from == Decimal("1000")
    assert parsed.search.price_to == Decimal("10000")
    assert parsed.search.seller_type == "private"
    assert parsed.search.order == "created_at:desc"
    assert parsed.search.opaque_query == {"search[filter_enum_state][0]": ["used"]}
    assert "utm_source" not in parsed.search.current_url
    assert "page=" not in parsed.search.current_url

    reparsed = source.parse_search_url(parsed.search.current_url)
    assert reparsed.search is not None
    expected = parsed.search.model_dump(exclude={"original_url", "current_url"})
    actual = reparsed.search.model_dump(exclude={"original_url", "current_url"})
    assert actual == expected


def test_listing_url_is_not_mistaken_for_search() -> None:
    parsed = OlxSource().parse_search_url(
        "https://www.olx.ua/d/uk/obyavlenie/test-IDabc.html?utm_source=x"
    )
    assert parsed.page_type is OlxPageType.LISTING
    assert parsed.listing_url == "https://www.olx.ua/d/uk/obyavlenie/test-IDabc.html"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/uk/elektronika/",
        "http://127.0.0.1/",
        "https://olx.ua.evil.example/",
        "https://user:pass@olx.ua/uk/",
        "ftp://www.olx.ua/uk/",
    ],
)
def test_unsafe_or_foreign_urls_are_rejected(url: str) -> None:
    with pytest.raises(AppError):
        OlxSource().parse_search_url(url)


def test_invalid_price_range_is_rejected() -> None:
    with pytest.raises(AppError) as error:
        OlxSource().parse_search_url(
            "https://www.olx.ua/uk/?search%5Bfilter_float_price%3Afrom%5D=20"
            "&search%5Bfilter_float_price%3Ato%5D=10"
        )
    assert error.value.code == "invalid_price_range"


@pytest.mark.asyncio
async def test_network_fetch_is_explicitly_not_configured() -> None:
    source = OlxSource(http_enabled=False)
    parsed = source.parse_search_url("https://www.olx.ua/uk/elektronika/")
    assert parsed.search is not None

    result = await source.fetch_search_page(parsed.search)

    assert result.state == "not_configured"
    assert result.error_code == "source_http_disabled"

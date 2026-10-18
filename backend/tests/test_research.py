from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import ResearchSession, User
from app.services.research import ResearchService
from app.sources.base import SourceResult, SourceState
from app.sources.olx import OlxSource
from app.sources.types import ListingCandidate


@pytest.mark.asyncio
async def test_research_waits_for_openrouter_configuration(db_session: AsyncSession) -> None:
    owner = User(telegram_id=111)
    db_session.add(owner)
    await db_session.commit()
    await db_session.refresh(owner)

    service = ResearchService(
        db_session,
        Settings(
            source_http_enabled=False,
            openrouter_api_key=None,
            openrouter_model=None,
            openrouter_research_model=None,
        ),
    )
    session = await service.create(
        owner,
        query="электровелосипед до 30000 грн",
        marketplaces=["olx", "prom"],
        max_items=10,
        budget_usd=Decimal("0.10"),
        model=None,
    )

    assert session.status == "waiting_configuration"
    assert session.stage == "waiting_for_openrouter"
    assert (await service.events(owner, session.id))[0].kind == "created"


@pytest.mark.asyncio
async def test_research_rejects_unknown_marketplace(db_session: AsyncSession) -> None:
    owner = User(telegram_id=111)
    db_session.add(owner)
    await db_session.commit()
    await db_session.refresh(owner)
    service = ResearchService(
        db_session,
        Settings(openrouter_api_key=None, openrouter_model=None, openrouter_research_model=None),
    )

    with pytest.raises(Exception) as error:
        await service.create(
            owner,
            query="телефон",
            marketplaces=["unknown"],
            max_items=10,
            budget_usd=Decimal("0.10"),
            model=None,
        )
    assert getattr(error.value, "code", None) == "unknown_marketplace"


def test_thinkpad_plan_builds_compact_olx_url_with_price_filter() -> None:
    service = ResearchService(db=None, settings=Settings())  # type: ignore[arg-type]
    specification = service._search_spec(
        "Lenovo ThinkPad",
        {"max_price": 5000, "currency": "UAH"},
    )

    assert specification is not None
    assert "q-Lenovo-ThinkPad" in specification.current_url
    assert "search%5Bfilter_float_price%3Ato%5D=5000" in specification.current_url
    assert "docker" not in specification.current_url.casefold()
    assert "python" not in specification.current_url.casefold()


def test_thinkpad_prefilter_rejects_unrelated_and_wrong_brand_cards() -> None:
    service = ResearchService(db=None, settings=Settings())  # type: ignore[arg-type]
    plan = {
        "product_type": "laptop",
        "brand": "Lenovo",
        "model_family": "ThinkPad",
        "max_price": 5000,
    }
    motorcycle = ListingCandidate(
        source_listing_id="IDmotor",
        canonical_url="https://www.olx.ua/d/uk/obyavlenie/motor-IDmotor.html",
        title="Мотоцикл Honda",
        price=Decimal("4500"),
        currency="UAH",
    )
    power_supply = ListingCandidate(
        source_listing_id="IDpower",
        canonical_url="https://www.olx.ua/d/uk/obyavlenie/power-IDpower.html",
        title="Блок живлення Lenovo ThinkPad",
        description="Сетевой адаптер для ноутбука Lenovo ThinkPad",
        price=Decimal("900"),
        currency="UAH",
    )
    asus = ListingCandidate(
        source_listing_id="IDasus",
        canonical_url="https://www.olx.ua/d/uk/obyavlenie/asus-IDasus.html",
        title="Ноутбук ASUS для роботи",
        price=Decimal("4500"),
        currency="UAH",
    )
    thinkpad = ListingCandidate(
        source_listing_id="IDthinkpad",
        canonical_url="https://www.olx.ua/d/uk/obyavlenie/thinkpad-IDthinkpad.html",
        title="Ноутбук Lenovo ThinkPad T480",
        price=Decimal("4500"),
        currency="UAH",
    )
    thinkpad_with_charger = ListingCandidate(
        source_listing_id="IDthinkpad-charger",
        canonical_url="https://www.olx.ua/d/uk/obyavlenie/thinkpad-charger-IDthinkpad.html",
        title="Ноутбук Lenovo ThinkPad T480, зарядка в комплекте",
        description="Новая батарея, блок питания и зарядка в комплекте.",
        price=Decimal("4500"),
        currency="UAH",
    )

    assert service._deterministic_relevance(motorcycle, plan)[0] == "reject"
    assert service._deterministic_relevance(power_supply, plan)[0] == "reject"
    assert service._deterministic_relevance(asus, plan)[0] == "reject"
    assert service._deterministic_relevance(thinkpad, plan)[0] == "accept"
    assert service._deterministic_relevance(thinkpad_with_charger, plan)[0] == "accept"


@pytest.mark.asyncio
async def test_multi_query_collection_deduplicates_candidates(db_session: AsyncSession) -> None:
    owner = User(telegram_id=222)
    db_session.add(owner)
    await db_session.commit()
    session = ResearchSession(
        owner_id=owner.id,
        query="синкпад до 5к",
        marketplaces=["olx"],
        model="vendor/model",
        status="running",
        stage="collecting_olx",
        max_items=2,
        budget_usd=Decimal("0.10"),
        search_plan={
            "product_type": "laptop",
            "brand": "Lenovo",
            "model_family": "ThinkPad",
            "max_price": 5000,
            "currency": "UAH",
            "search_queries": ["Lenovo ThinkPad", "ThinkPad T480"],
        },
    )
    db_session.add(session)
    await db_session.commit()

    candidate = ListingCandidate(
        source_listing_id="IDthinkpad",
        canonical_url="https://www.olx.ua/d/uk/obyavlenie/thinkpad-IDthinkpad.html",
        title="Lenovo ThinkPad T480",
        price=Decimal("4500"),
        currency="UAH",
    )
    service = ResearchService(db_session, Settings(scan_max_pages=1))
    service.source.fetch_search_page = AsyncMock(  # type: ignore[method-assign]
        return_value=SourceResult(
            state=SourceState.COMPLETE,
            data=[candidate.model_dump(mode="json")],
            metadata={"requested_url": "https://www.olx.ua/uk/list/q-ThinkPad/"},
        )
    )

    collected = await service._collect_olx(session)

    assert collected is not None and len(collected) == 1
    assert session.collected_count == 1
    assert len(session.executed_searches) == 2
    assert service.source.fetch_search_page.await_count == 2  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_restart_reuses_persisted_search_plan_without_another_ai_call(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = User(telegram_id=333)
    db_session.add(owner)
    await db_session.commit()
    session = ResearchSession(
        owner_id=owner.id,
        query="синкпад до 5к",
        marketplaces=["olx"],
        model="vendor/model",
        status="running",
        stage="collecting_olx",
        max_items=1,
        budget_usd=Decimal("0.10"),
        search_plan={
            "product_type": "laptop",
            "brand": "Lenovo",
            "model_family": "ThinkPad",
            "max_price": 5000,
            "currency": "UAH",
            "search_queries": ["Lenovo ThinkPad"],
        },
    )
    db_session.add(session)
    await db_session.commit()

    def unexpected_planner(*args: object, **kwargs: object) -> None:
        raise AssertionError("persisted plan must be reused after a restart")

    monkeypatch.setattr("app.services.research.OpenRouterClient", unexpected_planner)
    service = ResearchService(db_session, Settings())

    assert await service._ensure_search_plan(session, {"pricing": {}})


@pytest.mark.asyncio
async def test_olx_rejects_homepage_as_a_search_result() -> None:
    source = OlxSource(http_enabled=True)
    specification = source.parse_search_url("https://www.olx.ua/uk/list/q-ThinkPad/").search
    assert specification is not None
    source._fetch_html = AsyncMock(  # type: ignore[method-assign]
        return_value=SourceResult(
            state=SourceState.COMPLETE,
            data="<html><body>home</body></html>",
            metadata={"final_url": "https://www.olx.ua/uk/"},
        )
    )

    result = await source.fetch_search_page(specification)

    assert result.error_code == "search_result_mismatch"

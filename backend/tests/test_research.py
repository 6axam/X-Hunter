from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import User
from app.services.research import ResearchService


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

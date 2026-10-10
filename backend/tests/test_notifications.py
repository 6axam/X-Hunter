from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import DeliveryAttempt, NotificationEvent, User
from app.notifications.telegram import TelegramOutbox
from app.services.scanning import ScanService
from tests.test_scanning import candidate, make_search


@pytest.mark.asyncio
async def test_successful_delivery_marks_outbox_sent(db_session: AsyncSession) -> None:
    owner = User(telegram_id=111)
    db_session.add(owner)
    await db_session.commit()
    await db_session.refresh(owner)
    search = await make_search(db_session, owner)
    scanner = ScanService(db_session)
    await scanner.complete_baseline(search)
    await scanner.ingest(search, candidate())
    event = await db_session.scalar(select(NotificationEvent))
    assert event is not None
    bot = SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=42)))
    settings = Settings(owner_telegram_id=111, public_base_url="https://example.test")

    delivered = await TelegramOutbox(db_session, settings, bot).deliver(event, 111)  # type: ignore[arg-type]

    assert delivered is True
    assert event.status == "sent"
    attempt = await db_session.scalar(select(DeliveryAttempt))
    assert attempt is not None and attempt.message_id == "42"


@pytest.mark.asyncio
async def test_unknown_delivery_is_not_retried_automatically(db_session: AsyncSession) -> None:
    owner = User(telegram_id=111)
    db_session.add(owner)
    await db_session.commit()
    await db_session.refresh(owner)
    search = await make_search(db_session, owner)
    scanner = ScanService(db_session)
    await scanner.complete_baseline(search)
    await scanner.ingest(search, candidate())
    event = await db_session.scalar(select(NotificationEvent))
    assert event is not None
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=TimeoutError("lost response")))
    settings = Settings(owner_telegram_id=111, public_base_url="https://example.test")
    outbox = TelegramOutbox(db_session, settings, bot)  # type: ignore[arg-type]

    assert await outbox.deliver(event, 111) is False
    assert event.status == "delivery_unknown"
    assert await outbox.deliver_pending() == 0

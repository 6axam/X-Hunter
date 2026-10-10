from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.models import BudgetLedger


class BudgetService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def reserve_daily(
        self,
        owner_id: UUID,
        search_id: UUID,
        amount: Decimal,
        limit: Decimal,
    ) -> BudgetLedger:
        now = datetime.now(UTC)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        ledger = await self.db.scalar(
            select(BudgetLedger)
            .where(
                BudgetLedger.owner_id == owner_id,
                BudgetLedger.search_id == search_id,
                BudgetLedger.period_start == start,
            )
            .with_for_update()
        )
        if ledger is None:
            ledger = BudgetLedger(
                owner_id=owner_id,
                search_id=search_id,
                period_start=start,
                period_end=end,
                reserved_usd=Decimal("0"),
                actual_usd=Decimal("0"),
                uncertain_usd=Decimal("0"),
            )
            self.db.add(ledger)
            await self.db.flush()
        committed = ledger.reserved_usd + ledger.actual_usd + ledger.uncertain_usd
        if amount <= 0 or committed + amount > limit:
            raise AppError(
                status_code=429,
                code="ai_budget_exceeded",
                message="Дневной лимит ИИ исчерпан.",
            )
        ledger.reserved_usd += amount
        await self.db.commit()
        return ledger

    async def settle(self, ledger: BudgetLedger, reserved: Decimal, actual: Decimal | None) -> None:
        ledger.reserved_usd = max(Decimal("0"), ledger.reserved_usd - reserved)
        if actual is None:
            ledger.uncertain_usd += reserved
        else:
            ledger.actual_usd += actual
        await self.db.commit()

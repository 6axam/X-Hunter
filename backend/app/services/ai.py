from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.budget import BudgetService
from app.ai.openrouter import OpenRouterClient
from app.models import AiEvaluation, ListingVersion, Search, SearchMatch

SCHEMA_VERSION = "listing-evaluation-v1"


class AiService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def evaluate_match(
        self,
        search: Search,
        match: SearchMatch,
        version: ListingVersion,
        client: OpenRouterClient,
        model_info: dict,
    ) -> AiEvaluation:
        model = str(search.ai_settings.get("model") or "")
        instruction = str(search.ai_settings.get("instruction") or "")
        canonical = json.dumps(
            {"rules": search.rules, "instruction": instruction},
            ensure_ascii=False,
            sort_keys=True,
        )
        instruction_hash = hashlib.sha256(canonical.encode()).hexdigest()
        cache_key = hashlib.sha256(
            f"{version.content_hash}:{model}:{instruction_hash}:{SCHEMA_VERSION}".encode()
        ).hexdigest()
        cached = await self.db.scalar(
            select(AiEvaluation).where(AiEvaluation.cache_key == cache_key)
        )
        if cached:
            return cached

        pricing = model_info.get("pricing") or {}
        prompt_price = self._price(pricing.get("prompt"))
        completion_price = self._price(pricing.get("completion"))
        request_price = self._price(pricing.get("request"))
        if prompt_price is None or completion_price is None or request_price is None:
            raise ValueError("OpenRouter model price is unknown")
        reserve = max(
            Decimal("0.000001"),
            prompt_price * 5_000 + completion_price * 700 + request_price,
        )
        limit = Decimal(str(search.ai_settings.get("daily_budget_usd", "0.10")))
        ledger = await BudgetService(self.db).reserve_daily(
            search.owner_id, search.id, reserve, limit
        )
        evaluation = AiEvaluation(
            listing_id=match.listing_id,
            search_id=search.id,
            cache_key=cache_key,
            schema_version=SCHEMA_VERSION,
            model=model,
            instruction_hash=instruction_hash,
            status="pending",
        )
        self.db.add(evaluation)
        await self.db.commit()
        try:
            result, usage = await client.analyze(
                model=model,
                title=version.title,
                description=version.description,
                attributes=version.attributes,
                instruction=instruction,
            )
            actual = (
                prompt_price * usage["prompt_tokens"]
                + completion_price * usage["completion_tokens"]
                + request_price
            )
        except Exception:
            evaluation.status = "error"
            await BudgetService(self.db).settle(ledger, reserve, None)
            await self.db.commit()
            raise
        evaluation.status = "complete"
        evaluation.decision = result.decision
        evaluation.value_label = result.value_label
        evaluation.result = result.model_dump(mode="json")
        evaluation.input_tokens = usage["prompt_tokens"]
        evaluation.output_tokens = usage["completion_tokens"]
        evaluation.cost_usd = actual
        await BudgetService(self.db).settle(ledger, reserve, actual)
        await self.db.commit()
        return evaluation

    @staticmethod
    def _price(value: object) -> Decimal | None:
        try:
            return Decimal(str(value))
        except Exception:
            return None

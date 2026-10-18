from __future__ import annotations

import re
import time
from datetime import timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import and_, desc, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.openrouter import OpenRouterClient
from app.api.errors import AppError
from app.config import Settings
from app.logging import get_logger
from app.models import ListingVersion, ResearchEvent, ResearchItem, ResearchSession, User
from app.models.base import utcnow
from app.services.ai import AiService
from app.services.research_trace import ResearchTraceRecorder
from app.services.scanning import ScanService
from app.sources.base import SourceState
from app.sources.olx import OlxSearchSpec, OlxSource
from app.sources.types import ListingCandidate

MARKETPLACES = {
    "olx": {"label": "OLX.ua", "available": True, "reason": None},
    "prom": {"label": "Prom.ua", "available": False, "reason": "Адаптер ещё не проверен"},
    "rozetka": {"label": "Rozetka", "available": False, "reason": "Адаптер ещё не проверен"},
}
logger = get_logger(__name__)

_TOKEN_PATTERN = re.compile(r"[a-zа-яіїєґ0-9]+", re.IGNORECASE)
_PRODUCT_TERMS = {
    "laptop": {"laptop", "notebook", "ноутбук", "ноут", "лептоп"},
    "notebook": {"laptop", "notebook", "ноутбук", "ноут", "лептоп"},
    "ноутбук": {"laptop", "notebook", "ноутбук", "ноут", "лептоп"},
    "phone": {"phone", "smartphone", "телефон", "смартфон", "мобільний"},
    "smartphone": {"phone", "smartphone", "телефон", "смартфон", "мобільний"},
    "bicycle": {"bicycle", "bike", "велосипед", "вел"},
    "electric_bicycle": {"bicycle", "bike", "велосипед", "електровелосипед", "электровелосипед"},
    "mini_pc": {"mini", "pc", "мінпк", "минипк", "мініпк", "компьютер"},
    "mini-pc": {"mini", "pc", "мінпк", "минипк", "мініпк", "компьютер"},
    "desktop": {"pc", "компьютер", "системник", "системный"},
}
_UNRELATED_PRODUCT_TERMS = {
    "мотоцикл",
    "мото",
    "худі",
    "платье",
    "сукня",
    "тример",
    "сантехнік",
    "сантехник",
    "карта",
    "послуги",
    "услуги",
    "блок",
    "живлення",
    "зарядка",
    "зарядний",
    "акумулятор",
    "батарея",
    "клавіатура",
    "клавиатура",
    "екран",
    "экран",
}
_ACCESSORY_TERMS = {
    "блок",
    "живлення",
    "зарядка",
    "зарядний",
    "зарядное",
    "акумулятор",
    "батарея",
    "клавіатура",
    "клавиатура",
    "екран",
    "экран",
    "адаптер",
    "adapter",
}
_BRAND_ALIASES = {
    "lenovo": {"lenovo", "леново"},
    "asus": {"asus", "асус"},
    "acer": {"acer", "асер"},
    "hp": {"hp", "hewlett", "packard"},
    "dell": {"dell", "делл"},
    "apple": {"apple", "macbook", "макбук"},
}
_FAMILY_ALIASES = {
    "thinkpad": {"thinkpad", "тинкпад", "синкпад"},
}


class ResearchService:
    """Durable sequential research runner. One item is committed after every model call."""

    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self.db = db
        self.settings = settings
        self.source = OlxSource(
            http_enabled=settings.source_http_enabled,
            browser_enabled=settings.source_browser_enabled,
        )
        self.trace = ResearchTraceRecorder(db, settings)

    async def create(
        self,
        owner: User,
        *,
        query: str,
        marketplaces: list[str],
        max_items: int,
        budget_usd: Decimal,
        model: str | None,
    ) -> ResearchSession:
        selected = list(
            dict.fromkeys(value.strip().lower() for value in marketplaces if value.strip())
        )
        if not selected:
            selected = ["olx"]
        invalid = [value for value in selected if value not in MARKETPLACES]
        if invalid:
            raise AppError(
                status_code=422, code="unknown_marketplace", message="Выбрана неизвестная площадка."
            )
        if "olx" not in selected:
            raise AppError(
                status_code=422,
                code="no_available_marketplace",
                message="Для MVP выберите OLX.ua.",
            )
        resolved_model = (
            model or self.settings.openrouter_research_model or self.settings.openrouter_model
        )
        configured = bool(self.settings.openrouter_api_key and resolved_model)
        session = ResearchSession(
            owner_id=owner.id,
            query=query.strip(),
            marketplaces=selected,
            model=resolved_model,
            status="queued" if configured else "waiting_configuration",
            stage="queued" if configured else "waiting_for_openrouter",
            max_items=max_items,
            budget_usd=budget_usd,
            compact_memory={"leaders": [], "market_notes": []},
        )
        self.db.add(session)
        await self.db.flush()
        await self._event(
            session,
            "created",
            "Исследование создано." if configured else "Нужны ключ OpenRouter и выбранная модель.",
            {"marketplaces": selected, "model": resolved_model},
        )
        await self.trace.record(
            session,
            stage="lifecycle",
            event_type="research_created",
            payload={
                "query": session.query,
                "marketplaces": selected,
                "model": resolved_model,
                "max_items": max_items,
                "budget_usd": budget_usd,
                "configured": configured,
            },
        )
        await self.db.commit()
        await self.db.refresh(session)
        return session

    async def list(self, owner: User) -> list[ResearchSession]:
        return list(
            await self.db.scalars(
                select(ResearchSession)
                .where(ResearchSession.owner_id == owner.id)
                .order_by(desc(ResearchSession.updated_at))
            )
        )

    async def get(self, owner: User, session_id: UUID) -> ResearchSession:
        session = await self.db.scalar(
            select(ResearchSession).where(
                ResearchSession.id == session_id, ResearchSession.owner_id == owner.id
            )
        )
        if not session:
            raise AppError(
                status_code=404, code="research_not_found", message="Исследование не найдено."
            )
        return session

    async def items(self, owner: User, session_id: UUID) -> list[ResearchItem]:
        await self.get(owner, session_id)
        return list(
            await self.db.scalars(
                select(ResearchItem)
                .where(ResearchItem.session_id == session_id)
                .order_by(ResearchItem.score.desc().nullslast(), ResearchItem.sequence)
            )
        )

    async def events(self, owner: User, session_id: UUID) -> list[ResearchEvent]:
        await self.get(owner, session_id)
        return list(
            await self.db.scalars(
                select(ResearchEvent)
                .where(ResearchEvent.session_id == session_id)
                .order_by(ResearchEvent.created_at.desc())
                .limit(60)
            )
        )

    async def action(self, owner: User, session_id: UUID, action: str) -> ResearchSession:
        session = await self.get(owner, session_id)
        if action == "pause" and session.status in {"queued", "running"}:
            session.status, session.stage, session.lease_expires_at = "paused", "paused", None
            await self._event(session, "paused", "Исследование приостановлено.")
        elif action == "resume" and session.status in {"paused", "waiting_configuration", "failed"}:
            if not self.settings.openrouter_api_key or not session.model:
                raise AppError(
                    status_code=409,
                    code="openrouter_not_configured",
                    message="Нужны ключ OpenRouter и модель.",
                )
            session.status, session.stage, session.error_code = "queued", "queued", None
            await self._event(session, "resumed", "Исследование поставлено в очередь.")
        elif action == "cancel" and session.status not in {"completed", "cancelled"}:
            session.status, session.stage, session.finished_at, session.lease_expires_at = (
                "cancelled",
                "cancelled",
                utcnow(),
                None,
            )
            await self._event(session, "cancelled", "Исследование остановлено.")
        else:
            raise AppError(
                status_code=409,
                code="research_action_invalid",
                message="Это действие сейчас недоступно.",
            )
        await self.trace.record(
            session,
            stage="persistence",
            event_type=f"research_{action}",
            payload={"status": session.status, "stage": session.stage},
            round_id=session.current_round or None,
        )
        await self.db.commit()
        return session

    async def run(self, session_id: UUID) -> str:
        now = utcnow()
        claimed = await self.db.execute(
            update(ResearchSession)
            .where(
                ResearchSession.id == session_id,
                or_(
                    ResearchSession.status == "queued",
                    and_(
                        ResearchSession.status == "running",
                        ResearchSession.lease_expires_at.is_not(None),
                        ResearchSession.lease_expires_at < now,
                    ),
                ),
            )
            .values(
                status="running", stage="preparing", lease_expires_at=now + timedelta(minutes=5)
            )
        )
        if not claimed.rowcount:
            logger.info(
                "Research session skipped because it is not runnable",
                extra={"research_id": session_id, "event": "research_skipped"},
            )
            return "skipped"
        await self.db.commit()
        session = await self.db.get(ResearchSession, session_id)
        if not session:
            return "skipped"
        resumed = bool(session.started_at or session.current_round or session.cursor)
        await self.trace.record(
            session,
            stage="persistence",
            event_type="worker_resumed" if resumed else "worker_claimed",
            payload={
                "status": session.status,
                "stage": session.stage,
                "cursor": session.cursor,
                "processed_count": session.processed_count,
                "current_round": session.current_round,
                "lease_expires_at": session.lease_expires_at,
                "resumed_after_restart": resumed,
            },
            round_id=session.current_round or None,
        )
        await self.db.commit()
        logger.info(
            "Research session claimed",
            extra={
                "research_id": session.id,
                "event": "research_claimed",
                "status": session.status,
            },
        )
        if not self.settings.openrouter_api_key or not session.model:
            session.status, session.stage, session.lease_expires_at = (
                "waiting_configuration",
                "waiting_for_openrouter",
                None,
            )
            await self._event(session, "configuration", "Нужны ключ OpenRouter и модель.")
            await self.db.commit()
            return session.status

        models = {
            item.get("id"): item
            for item in await OpenRouterClient(self.settings.openrouter_api_key).list_models()
        }
        model_info = models.get(session.model)
        if model_info is None:
            session.status, session.stage, session.error_code = (
                "waiting_configuration",
                "model_not_available",
                "model_not_available",
            )
            session.lease_expires_at = None
            await self._event(
                session,
                "configuration",
                "Выбранная модель не найдена в каталоге OpenRouter этого ключа.",
                {"model": session.model},
            )
            await self.db.commit()
            return session.status

        if not await self._ensure_search_plan(session, model_info):
            return session.status

        session.status, session.stage, session.started_at, session.lease_expires_at = (
            "running",
            "collecting_olx",
            session.started_at or utcnow(),
            utcnow() + timedelta(minutes=5),
        )
        await self.db.commit()
        while session.status == "running":
            current_time = utcnow()
            started_at = session.started_at
            if started_at and started_at.tzinfo is None:
                current_time = current_time.replace(tzinfo=None)
            if started_at and current_time - started_at > timedelta(
                seconds=self.settings.research_max_runtime_seconds
            ):
                await self._event(
                    session,
                    "runtime_limit",
                    "Достигнут лимит времени исследования; сохраняю частичный итог.",
                )
                await self._finalize(
                    session, model_info, fallback_reason="Достигнут лимит времени."
                )
                break
            round_state = await self._prepare_round(session)
            if round_state is None:
                break
            candidates = await self._round_candidates(session, round_state)
            if candidates is None:
                return session.status
            await self._evaluate_candidates(session, candidates, model_info)
            await self.db.refresh(session)
            if session.status != "running":
                break
            session.compact_memory = await self._memory(session)
            session.market_statistics = await self._market_statistics(session)
            await self.trace.record(
                session,
                stage="market_statistics",
                event_type="market_statistics_calculated",
                round_id=session.current_round or None,
                payload={
                    "statistics": session.market_statistics,
                    "comparison_groups": [
                        {
                            "name": "evaluated_uah_listings",
                            "sample_size": session.market_statistics.get("sample_size", 0),
                            "currency": session.market_statistics.get("currency"),
                        }
                    ],
                    "comparability_limitations": session.market_statistics.get("reason"),
                },
            )
            await self._complete_round(session, round_state)
            decision = await self._supervise(session, model_info)
            if decision is None or session.status != "running":
                break
            if decision["action"] == "REQUEST_CLARIFICATION":
                session.status, session.stage, session.lease_expires_at = (
                    "needs_clarification",
                    "needs_clarification",
                    None,
                )
                await self._event(
                    session,
                    "clarification",
                    decision.get("clarification_question") or decision["reason"],
                )
                await self.db.commit()
                break
            if decision["action"] == "FINALIZE" or not decision.get("next_strategies"):
                await self._finalize(session, model_info)
                break
            if session.current_round >= self.settings.research_max_rounds:
                await self._event(session, "round_limit", "Достигнут лимит поисковых раундов.")
                await self._finalize(session, model_info)
                break
            await self._start_next_round(session, decision)
        logger.info(
            "Research session ended",
            extra={
                "research_id": session.id,
                "event": "research_finished",
                "status": session.status,
            },
        )
        return session.status

    def _pricing(self, model_info: dict[str, Any]) -> tuple[Decimal, Decimal, Decimal] | None:
        pricing = model_info.get("pricing") or {}
        prompt_price = AiService._price(pricing.get("prompt"))
        completion_price = AiService._price(pricing.get("completion"))
        if prompt_price is None or completion_price is None:
            return None
        return (
            prompt_price,
            completion_price,
            AiService._price(pricing.get("request")) or Decimal("0"),
        )

    async def _budget_error(self, session: ResearchSession, stage: str, message: str) -> None:
        session.status, session.stage, session.error_code, session.lease_expires_at = (
            "budget_exhausted",
            stage,
            "budget_exhausted",
            None,
        )
        await self._event(session, "budget", message)
        await self.db.commit()

    def _record_stage_usage(
        self,
        session: ResearchSession,
        stage: str,
        *,
        estimate: Decimal,
        actual: Decimal,
        usage: dict[str, int],
    ) -> None:
        entries = dict(session.stage_usage or {})
        previous = dict(entries.get(stage) or {})
        previous["calls"] = int(previous.get("calls", 0)) + 1
        previous["estimated_cost_usd"] = str(
            Decimal(str(previous.get("estimated_cost_usd", "0"))) + estimate
        )
        previous["actual_cost_usd"] = str(
            Decimal(str(previous.get("actual_cost_usd", "0"))) + actual
        )
        for key in ("prompt_tokens", "completion_tokens", "cached_input_tokens"):
            previous[key] = int(previous.get(key, 0)) + int(usage.get(key, 0))
        entries[stage] = previous
        session.stage_usage = entries
        session.estimated_cost_usd += estimate
        session.actual_cost_usd += actual

    async def _ensure_technical_reasoning(
        self, session: ResearchSession, model_info: dict[str, Any]
    ) -> bool:
        if session.technical_reasoning:
            return True
        prices = self._pricing(model_info)
        if prices is None:
            session.status, session.stage, session.error_code, session.lease_expires_at = (
                "failed",
                "technical_pricing_unknown",
                "pricing_unknown",
                None,
            )
            await self._event(
                session, "technical_error", "Невозможно оценить цену технического анализа."
            )
            await self.db.commit()
            return False
        prompt_price, completion_price, request_price = prices
        estimate = prompt_price * 2_000 + completion_price * 3_200 + request_price
        if session.actual_cost_usd + estimate > session.budget_usd:
            await self._budget_error(
                session,
                "technical_budget_exhausted",
                "Лимит не покрывает технический анализ задачи.",
            )
            return False
        session.stage = "understanding_requirements"
        await self._event(
            session, "technical_reasoning", "ИИ разбирает требования и направления поиска."
        )
        await self.trace.record(
            session,
            stage="technical_reasoning",
            event_type="technical_reasoning_started",
            payload={
                "input": {"query": session.query, "model": session.model},
                "estimated_cost_usd": estimate,
            },
        )
        await self.db.commit()
        started = time.perf_counter()
        try:
            result, usage = await OpenRouterClient(
                self.settings.openrouter_api_key or ""
            ).reason_about_requirements(model=session.model or "", query=session.query)
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            session.status, session.stage, session.error_code, session.lease_expires_at = (
                "failed",
                "technical_reasoning_failed",
                "technical_reasoning_failed",
                None,
            )
            await self._event(
                session,
                "technical_error",
                "Не удалось осмыслить требования.",
                {"type": type(exc).__name__},
            )
            await self.trace.record(
                session,
                stage="technical_reasoning",
                event_type="technical_reasoning_failed",
                duration_ms=duration_ms,
                payload={"error_type": type(exc).__name__},
            )
            await self.db.commit()
            return False
        actual = (
            prompt_price * usage["prompt_tokens"]
            + completion_price * usage["completion_tokens"]
            + request_price
        )
        needs_critical_clarification = self._needs_critical_clarification(result.model_dump())
        reasoning_data = result.model_dump(mode="json")
        # RAM/load, storage and delivery questions are useful uncertainty, but do
        # not block a buyer who already gave a usable product category and budget.
        # They remain visible in ``unknowns`` for later evaluation.
        if result.needs_clarification and not needs_critical_clarification:
            reasoning_data["needs_clarification"] = False
        session.technical_reasoning = reasoning_data
        session.product_hypotheses = (
            result.candidate_product_families
            and [value.model_dump(mode="json") for value in result.candidate_product_families]
            or []
        )
        await self.trace.record(
            session,
            stage="technical_reasoning",
            event_type="technical_reasoning_completed",
            duration_ms=int((time.perf_counter() - started) * 1000),
            payload={
                "input": {"query": session.query, "model": session.model},
                "output": reasoning_data,
                "product_hypotheses": session.product_hypotheses,
                "priorities": [
                    {
                        "family_or_model": item.get("family_or_model"),
                        "priority": item.get("priority"),
                        "confidence": item.get("confidence"),
                    }
                    for item in session.product_hypotheses
                ],
                "tradeoffs": reasoning_data.get("tradeoffs", []),
                "unknowns": reasoning_data.get("unknowns", []),
                "usage": usage,
                "actual_cost_usd": actual,
            },
        )
        self._record_stage_usage(
            session, "technical_reasoning", estimate=estimate, actual=actual, usage=usage
        )
        session.lease_expires_at = utcnow() + timedelta(minutes=5)
        if needs_critical_clarification:
            session.status, session.stage, session.lease_expires_at = (
                "needs_clarification",
                "needs_clarification",
                None,
            )
            await self._event(
                session,
                "clarification",
                result.clarification_question or "Нужно уточнить требования.",
            )
        else:
            if result.needs_clarification:
                await self._event(
                    session,
                    "clarification_deferred",
                    "Необязательное уточнение сохранено как риск; поиск продолжается.",
                )
            await self._event(
                session,
                "technical_ready",
                "Сохранены технические требования и гипотезы моделей.",
                {"hypotheses": len(session.product_hypotheses)},
            )
        await self.db.commit()
        return not needs_critical_clarification

    @staticmethod
    def _needs_critical_clarification(reasoning: dict[str, Any]) -> bool:
        """Only stop when a marketplace direction cannot be chosen safely."""
        category = str(reasoning.get("product_category") or "").casefold().strip()
        generic_categories = {"товар", "річ", "вещь", "product", "item", "unknown"}
        return bool(reasoning.get("needs_clarification")) and (
            not category or category in generic_categories or not reasoning.get("search_strategy")
        )

    async def _ensure_search_plan(
        self, session: ResearchSession, model_info: dict[str, Any]
    ) -> bool:
        """Create exactly one durable plan before any marketplace URL is built."""
        if session.search_plan:
            return True
        if not await self._ensure_technical_reasoning(session, model_info):
            return False
        prices = self._pricing(model_info)
        if prices is None:
            session.status, session.stage, session.error_code, session.lease_expires_at = (
                "failed",
                "planning_pricing_unknown",
                "pricing_unknown",
                None,
            )
            await self._event(
                session, "planning_error", "Невозможно безопасно оценить цену планирования."
            )
            await self.db.commit()
            return False
        prompt_price, completion_price, request_price = prices
        estimate = prompt_price * 1_200 + completion_price * 800 + request_price
        if session.actual_cost_usd + estimate > session.budget_usd:
            session.status, session.stage, session.error_code, session.lease_expires_at = (
                "budget_exhausted",
                "planning_budget_exhausted",
                "budget_exhausted",
                None,
            )
            await self._event(session, "budget", "Лимит расходов не покрывает планирование поиска.")
            await self.db.commit()
            return False
        session.stage = "planning_search"
        await self._event(session, "planning", "ИИ формирует поисковый план до обращения к OLX.")
        await self.trace.record(
            session,
            stage="search_planner",
            event_type="search_planner_started",
            payload={
                "input": {
                    "query": session.query,
                    "technical_reasoning": session.technical_reasoning,
                    "product_hypotheses": session.product_hypotheses,
                },
                "estimated_cost_usd": estimate,
            },
        )
        await self.db.commit()
        started = time.perf_counter()
        try:
            client = OpenRouterClient(self.settings.openrouter_api_key or "")
            plan, usage = await client.plan_search(
                model=session.model or "",
                query=session.query,
                technical_reasoning=session.technical_reasoning,
            )
        except Exception as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            session.status, session.stage, session.error_code, session.lease_expires_at = (
                "failed",
                "planning_failed",
                "planning_failed",
                None,
            )
            await self._event(
                session,
                "planning_error",
                "Не удалось сформировать поисковый план; OLX не запускался.",
                {"type": type(exc).__name__},
            )
            await self.trace.record(
                session,
                stage="search_planner",
                event_type="search_planner_failed",
                duration_ms=duration_ms,
                payload={"error_type": type(exc).__name__},
            )
            await self.db.commit()
            logger.warning(
                "Search planning failed",
                extra={
                    "research_id": session.id,
                    "event": "search_planning_failed",
                    "status": type(exc).__name__,
                },
            )
            return False
        actual = (
            prompt_price * usage["prompt_tokens"]
            + completion_price * usage["completion_tokens"]
            + request_price
        )
        plan_data = plan.model_dump(mode="json")
        reasoning = session.technical_reasoning
        plan_data["hard_requirements"] = list(
            dict.fromkeys([*plan.hard_requirements, *reasoning.get("hard_constraints", [])])
        )
        plan_data["soft_requirements"] = list(
            dict.fromkeys([*plan.soft_requirements, *reasoning.get("soft_preferences", [])])
        )
        plan_data["excluded_attributes"] = reasoning.get("excluded_attributes", [])
        session.search_plan = plan_data
        reasoning_strategies = {
            item.get("phrase"): item
            for item in reasoning.get("search_strategy", [])
            if item.get("phrase")
        }
        planned_strategies = [
            reasoning_strategies.get(
                phrase,
                {
                    "phrase": phrase,
                    "category": plan_data.get("product_type"),
                    "filters": [{"key": "price_to", "value": str(plan_data["max_price"])}]
                    if plan_data.get("max_price")
                    else [],
                    "rationale": " ".join(
                        [
                            "Фраза сформирована Search Planner из",
                            "структурированных требований.",
                        ]
                    ),
                },
            )
            for phrase in plan.search_queries
        ]
        await self.trace.record(
            session,
            stage="search_planner",
            event_type="search_planner_completed",
            duration_ms=int((time.perf_counter() - started) * 1000),
            payload={
                "input": {
                    "query": session.query,
                    "technical_reasoning": session.technical_reasoning,
                },
                "output": plan_data,
                "strategies": planned_strategies,
                "usage": usage,
                "actual_cost_usd": actual,
            },
        )
        session.planning_estimated_cost_usd = estimate
        session.planning_actual_cost_usd = actual
        session.planning_input_tokens = usage["prompt_tokens"]
        session.planning_output_tokens = usage["completion_tokens"]
        self._record_stage_usage(
            session, "initial_planning", estimate=estimate, actual=actual, usage=usage
        )
        session.lease_expires_at = utcnow() + timedelta(minutes=5)
        if plan.needs_clarification:
            session.status, session.stage, session.lease_expires_at = (
                "needs_clarification",
                "needs_clarification",
                None,
            )
            await self._event(
                session,
                "clarification",
                plan.clarification_question or "Уточните, какой тип товара нужен.",
                {"plan": session.search_plan},
            )
        else:
            await self._event(
                session,
                "planned",
                "ИИ сформировал поисковые фразы и ограничения.",
                {"plan": session.search_plan},
            )
        await self.db.commit()
        logger.info(
            "Search plan saved",
            extra={
                "research_id": session.id,
                "event": "search_planning_completed",
                "status": f"queries={len(plan.search_queries)}",
            },
        )
        return not plan.needs_clarification

    def _search_spec(self, phrase: str, plan: dict[str, Any]) -> OlxSearchSpec | None:
        """Build OLX URL only from a compact planner phrase and structured price data."""
        path_phrase = quote("-".join(phrase.split()), safe="-")
        parsed = self.source.parse_search_url(f"https://www.olx.ua/uk/list/q-{path_phrase}/")
        if not parsed.search:
            return None
        max_price = plan.get("max_price")
        currency = plan.get("currency")
        specification = parsed.search.model_copy(
            update={
                "price_to": Decimal(str(max_price)) if max_price else None,
                "currency": currency if currency == "UAH" else None,
            }
        )
        specification.current_url = self.source.build_search_url(specification)
        return specification

    async def _collect_olx(
        self,
        session: ResearchSession,
        queries: list[str] | None = None,
        *,
        round_number: int | None = None,
    ) -> list[ListingCandidate] | None:
        queries = list(dict.fromkeys(queries or session.search_plan.get("search_queries") or []))
        executed = {
            str(record.get("query", "")).casefold().strip()
            for record in session.executed_searches
            if record.get("query")
        }
        # A completed phrase is immutable evidence for this session.  Re-running it
        # after a restart only wastes OLX requests and can change the candidate set.
        queries = [query for query in queries if query.casefold().strip() not in executed]
        if not queries:
            await self._event(session, "collection_skipped", "Новых поисковых фраз для OLX нет.")
            await self.db.commit()
            return []

        candidates: list[ListingCandidate] = []
        seen: set[str] = set()
        failures: list[dict[str, str]] = []
        # Each planned phrase gets its first page.  Only later pagination is bounded by
        # the global limit, otherwise a broad first phrase can hide model-family variants.
        max_candidates = max(session.max_items * 5, 25) * len(queries)
        for phrase in queries:
            query_id = f"r{round_number or 0}-q{len(session.executed_searches) + 1}"
            specification = self._search_spec(phrase, session.search_plan)
            if specification is None:
                failures.append({"query": phrase, "code": "query_build_failed"})
                await self.trace.record(
                    session,
                    stage="olx",
                    event_type="olx_query_build_failed",
                    round_id=round_number,
                    query_id=query_id,
                    payload={"query": phrase, "reason": "query_build_failed"},
                )
                continue
            search_record: dict[str, Any] = {
                "query_id": query_id,
                "query": phrase,
                "url": specification.current_url,
                "price_to": str(specification.price_to) if specification.price_to else None,
                "pages": [],
            }
            if round_number is not None:
                search_record["round"] = round_number
            for page in range(1, self.settings.scan_max_pages + 1):
                if page > 1 and len(candidates) >= max_candidates:
                    break
                started = time.perf_counter()
                result = await self.source.fetch_search_page(specification, page)
                duration_ms = int((time.perf_counter() - started) * 1000)
                page_record = {
                    "page": page,
                    "state": result.state.value,
                    "url": result.metadata.get("requested_url", specification.current_url),
                    "final_url": result.metadata.get("final_url"),
                    "results": len(result.data or []),
                    "error_code": result.error_code,
                    "duration_ms": duration_ms,
                }
                search_record["pages"].append(page_record)
                await self.trace.record(
                    session,
                    stage="olx",
                    event_type="olx_search_page_completed",
                    round_id=round_number,
                    query_id=query_id,
                    duration_ms=duration_ms,
                    payload={
                        "query": phrase,
                        "requested_url": result.metadata.get(
                            "requested_url", specification.current_url
                        ),
                        "applied_filters": specification.model_dump(mode="json"),
                        "final_url": result.metadata.get("final_url"),
                        "page": page,
                        "results_count": len(result.data or []),
                        "state": result.state.value,
                        "error_code": result.error_code,
                        "message": result.message,
                    },
                )
                logger.info(
                    "OLX planned search page processed",
                    extra={
                        "research_id": session.id,
                        "event": "olx_planned_search",
                        "status": (
                            f"query={phrase}; page={page}; results={page_record['results']}; "
                            f"state={result.state.value}; error={result.error_code or '-'}"
                        ),
                    },
                )
                if result.state is not SourceState.COMPLETE:
                    failures.append(
                        {"query": phrase, "code": result.error_code or result.state.value}
                    )
                    break
                page_candidates = [
                    ListingCandidate.model_validate(raw) for raw in result.data or []
                ]
                for candidate in page_candidates:
                    identifier = candidate.source_listing_id or candidate.canonical_url
                    if identifier in seen:
                        await self.trace.record(
                            session,
                            stage="persistence",
                            event_type="duplicate_candidate_skipped",
                            round_id=round_number,
                            query_id=query_id,
                            listing_id=candidate.source_listing_id,
                            payload={
                                "title": candidate.title,
                                "canonical_url": candidate.canonical_url,
                                "reason": "duplicate_within_collection",
                            },
                        )
                        continue
                    seen.add(identifier)
                    candidates.append(candidate)
                if len(page_candidates) == 0:
                    break
            session.executed_searches = [*session.executed_searches, search_record]
            await self.db.commit()

        session.collected_count += len(candidates)
        if not candidates:
            await self._event(
                session,
                "collection_empty",
                "OLX не дал новых кандидатов по этому направлению.",
                {"failures": failures},
            )
            await self.db.commit()
            return []
        await self._event(
            session,
            "collected",
            f"Подтверждено кандидатов OLX: {len(candidates)}.",
            {"queries": queries, "collected": len(candidates), "failures": failures},
        )
        await self.db.commit()
        return candidates

    async def _prepare_round(self, session: ResearchSession) -> dict[str, Any] | None:
        """Return the unfinished durable round, or create the initial one once."""
        rounds = list(session.research_rounds or [])
        for round_state in rounds:
            if round_state.get("status") == "active":
                await self.trace.record(
                    session,
                    stage="persistence",
                    event_type="active_round_restored",
                    round_id=round_state.get("number"),
                    payload={
                        "round": round_state,
                        "cursor": session.cursor,
                        "processed_count": session.processed_count,
                    },
                )
                await self.db.commit()
                return round_state
        if rounds:
            return None
        strategies = [
            {
                "phrase": phrase,
                "category": session.search_plan.get("product_type"),
                "filters": [],
                "priority": index + 1,
                "expected_result_type": session.search_plan.get("product_type") or "товар",
                "goal": "Проверка первоначального направления",
                "rationale": "Фраза из первоначального поискового плана.",
            }
            for index, phrase in enumerate(session.search_plan.get("search_queries") or [])
        ][: self.settings.research_max_queries_per_round]
        if not strategies:
            session.status, session.stage, session.error_code, session.lease_expires_at = (
                "failed",
                "planning_invalid",
                "planning_invalid",
                None,
            )
            await self._event(session, "planning_error", "План не содержит поисковых фраз для OLX.")
            await self.db.commit()
            return None
        round_state = {
            "number": 1,
            "status": "active",
            "strategies": strategies,
            "started_at": utcnow().isoformat(),
            "collected_count": 0,
            "evaluated_count": 0,
            "new_candidates": [],
        }
        session.current_round = 1
        session.research_rounds = [round_state]
        session.stage = "collecting_olx"
        await self._event(session, "round_started", "Начат поисковый раунд 1.", {"round": 1})
        await self.trace.record(
            session,
            stage="search_planner",
            event_type="research_round_planned",
            round_id=1,
            payload={"strategies": strategies},
        )
        await self.db.commit()
        return round_state

    async def _round_candidates(
        self, session: ResearchSession, round_state: dict[str, Any]
    ) -> list[ListingCandidate] | None:
        saved = round_state.get("new_candidates") or []
        if saved:
            await self.trace.record(
                session,
                stage="persistence",
                event_type="candidate_checkpoint_restored",
                round_id=round_state.get("number"),
                payload={"candidate_count": len(saved)},
            )
            await self.db.commit()
            return [ListingCandidate.model_validate(candidate) for candidate in saved]
        phrases = [
            str(strategy.get("phrase", "")).strip()
            for strategy in round_state.get("strategies") or []
            if str(strategy.get("phrase", "")).strip()
        ]
        await self._event(
            session,
            "collecting",
            f"Раунд {round_state['number']}: ищу подходящие объявления на OLX.ua.",
            {"round": round_state["number"], "queries": phrases},
        )
        await self.db.commit()
        candidates = await self._collect_olx(
            session, phrases, round_number=int(round_state["number"])
        )
        if candidates is None:
            return None
        round_state["new_candidates"] = [
            candidate.model_dump(mode="json") for candidate in candidates
        ]
        round_state["collected_count"] = len(candidates)
        await self._replace_round(session, round_state)
        return candidates

    async def _evaluate_candidates(
        self,
        session: ResearchSession,
        candidates: list[ListingCandidate],
        model_info: dict[str, Any],
    ) -> None:
        for candidate in candidates:
            await self.db.refresh(session)
            if session.status != "running" or session.processed_count >= session.max_items:
                return
            decision, reason = self._deterministic_relevance(candidate, session.search_plan)
            await self.trace.record(
                session,
                stage="prefilter",
                event_type="prefilter_decision",
                round_id=session.current_round or None,
                listing_id=candidate.source_listing_id,
                payload={
                    "phase": "search_card",
                    "listing": {
                        "title": candidate.title,
                        "price": candidate.price,
                        "currency": candidate.currency,
                        "canonical_url": candidate.canonical_url,
                    },
                    "decision": decision,
                    "triggered_rules": [self._prefilter_rule_code(reason)],
                    "reason": reason,
                },
            )
            if decision == "reject":
                await self._reject_before_analysis(session, candidate, reason)
                continue
            started = time.perf_counter()
            details = await self.source.fetch_listing(candidate.canonical_url)
            detail_duration_ms = int((time.perf_counter() - started) * 1000)
            await self.trace.record(
                session,
                stage="olx",
                event_type="olx_listing_completed",
                round_id=session.current_round or None,
                listing_id=candidate.source_listing_id,
                duration_ms=detail_duration_ms,
                payload={
                    "requested_url": candidate.canonical_url,
                    "final_url": details.metadata.get("final_url"),
                    "state": details.state.value,
                    "error_code": details.error_code,
                    "message": details.message,
                },
            )
            if details.state is SourceState.COMPLETE and details.data:
                candidate = ListingCandidate.model_validate(details.data)
                decision, reason = self._deterministic_relevance(candidate, session.search_plan)
            else:
                decision, reason = "uncertain", "Не удалось подтвердить полную карточку OLX."
                logger.warning(
                    "OLX listing detail was unavailable before AI analysis",
                    extra={
                        "research_id": session.id,
                        "event": "olx_listing_detail_unavailable",
                        "status": details.error_code or details.state,
                    },
                )
            await self.trace.record(
                session,
                stage="prefilter",
                event_type="prefilter_decision",
                round_id=session.current_round or None,
                listing_id=candidate.source_listing_id,
                payload={
                    "phase": "full_listing",
                    "listing": {
                        "title": candidate.title,
                        "price": candidate.price,
                        "currency": candidate.currency,
                        "attributes": candidate.attributes,
                    },
                    "decision": decision,
                    "triggered_rules": [self._prefilter_rule_code(reason)],
                    "reason": reason,
                },
            )
            if decision == "reject":
                await self._reject_before_analysis(session, candidate, reason)
                continue
            if decision == "uncertain":
                decision = await self._check_uncertain_relevance(
                    session, candidate, model_info, reason
                )
                if decision != "accept":
                    continue
            listing, version, _, _ = await ScanService(self.db).upsert_listing(candidate)
            item = await self.db.scalar(
                select(ResearchItem).where(
                    ResearchItem.session_id == session.id, ResearchItem.listing_id == listing.id
                )
            )
            if item and item.status == "complete":
                await self.trace.record(
                    session,
                    stage="persistence",
                    event_type="duplicate_research_item_skipped",
                    round_id=session.current_round or None,
                    listing_id=listing.id,
                    payload={"item_id": item.id, "reason": "already_complete"},
                )
                await self.db.commit()
                continue
            if item is None:
                item = ResearchItem(
                    session_id=session.id,
                    listing_id=listing.id,
                    sequence=session.cursor + 1,
                    listing_snapshot={
                        "title": version.title,
                        "price": str(version.price) if version.price is not None else None,
                        "currency": version.currency,
                        "city": version.city,
                        "url": listing.canonical_url,
                    },
                )
                self.db.add(item)
                await self.db.flush()
                await self.trace.record(
                    session,
                    stage="persistence",
                    event_type="research_item_created",
                    round_id=session.current_round or None,
                    listing_id=listing.id,
                    payload={"item_id": item.id, "sequence": item.sequence},
                )
            await self._evaluate_item(session, item, version, model_info, candidate)

    async def _replace_round(self, session: ResearchSession, round_state: dict[str, Any]) -> None:
        rounds = list(session.research_rounds or [])
        session.research_rounds = [
            round_state if value.get("number") == round_state.get("number") else value
            for value in rounds
        ]
        await self.trace.record(
            session,
            stage="persistence",
            event_type="round_checkpoint_saved",
            round_id=round_state.get("number"),
            payload={
                "status": round_state.get("status"),
                "collected_count": round_state.get("collected_count"),
                "evaluated_count": round_state.get("evaluated_count"),
                "candidate_checkpoint_count": len(round_state.get("new_candidates") or []),
            },
        )
        await self.db.commit()

    async def _complete_round(self, session: ResearchSession, round_state: dict[str, Any]) -> None:
        round_state["status"] = "completed"
        round_state["completed_at"] = utcnow().isoformat()
        round_state["evaluated_count"] = session.processed_count
        # Candidate data is useful only for crash recovery while active; keeping it
        # after a completed round would bloat each session with raw OLX payloads.
        round_state.pop("new_candidates", None)
        await self._replace_round(session, round_state)
        await self._event(
            session,
            "round_completed",
            f"Раунд {round_state['number']} завершён: собрано {round_state['collected_count']}.",
            {"round": round_state["number"], "collected": round_state["collected_count"]},
        )
        await self.trace.record(
            session,
            stage="persistence",
            event_type="round_completed",
            round_id=round_state.get("number"),
            payload={
                "collected_count": round_state.get("collected_count"),
                "evaluated_count": round_state.get("evaluated_count"),
            },
        )
        await self.db.commit()

    async def _start_next_round(self, session: ResearchSession, decision: dict[str, Any]) -> None:
        strategies = list(decision.get("next_strategies") or [])[
            : self.settings.research_max_queries_per_round
        ]
        number = session.current_round + 1
        round_state = {
            "number": number,
            "status": "active",
            "strategies": strategies,
            "started_at": utcnow().isoformat(),
            "collected_count": 0,
            "evaluated_count": 0,
            "new_candidates": [],
            "supervisor_action": decision["action"],
        }
        session.current_round = number
        session.research_rounds = [*session.research_rounds, round_state]
        session.stage, session.lease_expires_at = "collecting_olx", utcnow() + timedelta(minutes=5)
        await self._event(
            session,
            "round_started",
            f"Supervisor выбрал {decision['action']}; начат раунд {number}.",
            {"round": number, "reason": decision["reason"], "action": decision["action"]},
        )
        await self.trace.record(
            session,
            stage="search_planner",
            event_type="research_round_planned",
            round_id=number,
            payload={
                "supervisor_action": decision["action"],
                "reason": decision["reason"],
                "strategies": strategies,
            },
        )
        await self.db.commit()

    async def _supervisor_context(self, session: ResearchSession) -> dict[str, Any]:
        items = list(
            await self.db.scalars(
                select(ResearchItem)
                .where(ResearchItem.session_id == session.id, ResearchItem.status == "complete")
                .order_by(ResearchItem.score.desc().nullslast())
                .limit(8)
            )
        )
        return {
            "round": session.current_round,
            "round_limit": self.settings.research_max_rounds,
            "executed_queries": [item.get("query") for item in session.executed_searches],
            "counts": {
                "collected": session.collected_count,
                "evaluated": session.processed_count,
                "shortlisted": session.accepted_count,
                "rejected_before_analysis": session.rejected_before_analysis_count,
            },
            "remaining_budget_usd": str(
                max(session.budget_usd - session.actual_cost_usd, Decimal("0"))
            ),
            "technical_requirements": session.technical_reasoning.get("technical_requirements", []),
            "hypotheses": session.product_hypotheses[:6],
            "memory": session.compact_memory,
            "market_statistics": session.market_statistics,
            "leaders": [
                {
                    "listing_id": str(item.listing_id),
                    "title": item.listing_snapshot.get("title"),
                    "price": item.listing_snapshot.get("price"),
                    "currency": item.listing_snapshot.get("currency"),
                    "score": item.score,
                    "recommendation": item.recommendation,
                    "summary": item.analysis.get("summary", "")[:240],
                    "risks": item.analysis.get("risks", [])[:3],
                }
                for item in items
            ],
        }

    async def _supervise(
        self, session: ResearchSession, model_info: dict[str, Any]
    ) -> dict[str, Any] | None:
        prices = self._pricing(model_info)
        if prices is None:
            await self._finalize(session, model_info, fallback_reason="Цена Supervisor неизвестна.")
            return None
        prompt_price, completion_price, request_price = prices
        estimate = prompt_price * 2_400 + completion_price * 1_000 + request_price
        # Reserve a small final-comparison call. A supervisor never consumes the
        # final safe step of a session's budget.
        final_reserve = prompt_price * 2_200 + completion_price * 1_200 + request_price
        if session.actual_cost_usd + estimate + final_reserve > session.budget_usd:
            await self._event(
                session, "budget", "Остаток бюджета сохранён для итогового сравнения."
            )
            await self.db.commit()
            await self._finalize(
                session, model_info, fallback_reason="Лимит расходов почти исчерпан."
            )
            return None
        session.stage = "supervising"
        context = await self._supervisor_context(session)
        await self.trace.record(
            session,
            stage="supervisor",
            event_type="supervisor_started",
            round_id=session.current_round or None,
            payload={
                "input": {"query": session.query, "state": context},
                "estimated_cost_usd": estimate,
            },
        )
        await self.db.commit()
        started = time.perf_counter()
        try:
            result, usage = await OpenRouterClient(
                self.settings.openrouter_api_key or ""
            ).supervise_research(
                model=session.model or "",
                query=session.query,
                state=context,
            )
        except Exception as exc:
            await self._event(
                session,
                "supervisor_error",
                "Supervisor недоступен; формирую итог по сохранённым результатам.",
                {"type": type(exc).__name__},
            )
            await self.trace.record(
                session,
                stage="supervisor",
                event_type="supervisor_failed",
                round_id=session.current_round or None,
                duration_ms=int((time.perf_counter() - started) * 1000),
                payload={"input": context, "error_type": type(exc).__name__},
            )
            await self.db.commit()
            await self._finalize(
                session, model_info, fallback_reason="Supervisor временно недоступен."
            )
            return None
        actual = (
            prompt_price * usage["prompt_tokens"]
            + completion_price * usage["completion_tokens"]
            + request_price
        )
        self._record_stage_usage(
            session, "supervisor", estimate=estimate, actual=actual, usage=usage
        )
        decision = result.model_dump(mode="json")
        decision["round"] = session.current_round
        decision["created_at"] = utcnow().isoformat()
        session.supervisor_decisions = [*session.supervisor_decisions, decision]
        await self.trace.record(
            session,
            stage="supervisor",
            event_type="supervisor_completed",
            round_id=session.current_round or None,
            duration_ms=int((time.perf_counter() - started) * 1000),
            payload={
                "input": {"query": session.query, "state": context},
                "output": decision,
                "action": result.action,
                "reason": result.reason,
                "new_strategies": [
                    strategy.model_dump(mode="json") for strategy in result.next_strategies
                ],
                "usage": usage,
                "actual_cost_usd": actual,
            },
        )
        if result.action == "FINALIZE" and not context.get("leaders"):
            await self.trace.contradiction(
                session,
                code="supervisor_finalized_without_evaluated_candidates",
                message="Supervisor завершил исследование без проверенных лидеров.",
                round_id=session.current_round or None,
                evidence={"action": result.action, "leaders": []},
            )
        session.lease_expires_at = utcnow() + timedelta(minutes=5)
        await self._event(
            session,
            "supervisor",
            f"Supervisor: {result.reason}",
            {"action": result.action, "round": session.current_round},
        )
        await self.db.commit()
        return decision

    async def _finalize(
        self,
        session: ResearchSession,
        model_info: dict[str, Any],
        *,
        fallback_reason: str | None = None,
    ) -> None:
        if session.final_comparison:
            session.status, session.stage, session.finished_at, session.lease_expires_at = (
                "completed",
                "completed",
                session.finished_at or utcnow(),
                None,
            )
            await self.trace.record(
                session,
                stage="persistence",
                event_type="final_comparison_restored",
                round_id=session.current_round or None,
                payload={"final_comparison": session.final_comparison},
            )
            await self.db.commit()
            return
        context = await self._supervisor_context(session)
        prices = self._pricing(model_info)
        comparison: dict[str, Any]
        final_usage: dict[str, int] | None = None
        final_actual: Decimal | None = None
        final_duration_ms: int | None = None
        comparison_source = "fallback"
        if prices is not None:
            prompt_price, completion_price, request_price = prices
            estimate = prompt_price * 2_200 + completion_price * 1_200 + request_price
            if session.actual_cost_usd + estimate <= session.budget_usd:
                session.stage = "final_comparison"
                await self.trace.record(
                    session,
                    stage="final_comparison",
                    event_type="final_comparison_started",
                    round_id=session.current_round or None,
                    payload={
                        "input": {"query": session.query, "state": context},
                        "compared_options": context.get("leaders", []),
                        "estimated_cost_usd": estimate,
                    },
                )
                await self.db.commit()
                started = time.perf_counter()
                try:
                    result, usage = await OpenRouterClient(
                        self.settings.openrouter_api_key or ""
                    ).compare_finalists(
                        model=session.model or "", query=session.query, state=context
                    )
                    actual = (
                        prompt_price * usage["prompt_tokens"]
                        + completion_price * usage["completion_tokens"]
                        + request_price
                    )
                    self._record_stage_usage(
                        session, "final_comparison", estimate=estimate, actual=actual, usage=usage
                    )
                    comparison = result.model_dump(mode="json")
                    final_usage = usage
                    final_actual = actual
                    final_duration_ms = int((time.perf_counter() - started) * 1000)
                    comparison_source = "model"
                except Exception as exc:
                    final_duration_ms = int((time.perf_counter() - started) * 1000)
                    await self.trace.record(
                        session,
                        stage="final_comparison",
                        event_type="final_comparison_failed",
                        round_id=session.current_round or None,
                        duration_ms=final_duration_ms,
                        payload={
                            "input": context,
                            "error_type": type(exc).__name__,
                        },
                    )
                    fallback_reason = (
                        fallback_reason or f"Итоговая модель недоступна ({type(exc).__name__})."
                    )
                    comparison = self._fallback_comparison(context, fallback_reason)
            else:
                comparison = self._fallback_comparison(
                    context, fallback_reason or "Недостаточно бюджета для итоговой модели."
                )
        else:
            comparison = self._fallback_comparison(
                context, fallback_reason or "Цена итоговой модели неизвестна."
            )
        comparison["generated_at"] = utcnow().isoformat()
        comparison["market_statistics"] = session.market_statistics
        session.final_comparison = comparison
        await self.trace.record(
            session,
            stage="final_comparison",
            event_type="final_comparison_completed",
            round_id=session.current_round or None,
            duration_ms=final_duration_ms,
            payload={
                "input": {"query": session.query, "state": context},
                "compared_options": context.get("leaders", []),
                "output": comparison,
                "source": comparison_source,
                "usage": final_usage,
                "actual_cost_usd": final_actual,
            },
        )
        leader_ids = {str(item.get("listing_id")) for item in context.get("leaders", [])}
        leader_titles = {str(item.get("title")) for item in context.get("leaders", [])}
        leaders_by_id = {str(item.get("listing_id")): item for item in context.get("leaders", [])}
        for choice_name in ("best_overall", "best_budget", "best_performance", "safest"):
            choice = comparison.get(choice_name)
            if not choice:
                continue
            choice_id = str(choice.get("listing_id")) if choice.get("listing_id") else None
            choice_title = str(choice.get("title")) if choice.get("title") else None
            if (choice_id not in leader_ids) and (choice_title not in leader_titles):
                await self.trace.contradiction(
                    session,
                    code="final_winner_not_in_evaluated_candidates",
                    message="Финальный выбор отсутствует среди проверенных объявлений.",
                    round_id=session.current_round or None,
                    listing_id=choice_id,
                    evidence={
                        "choice": choice_name,
                        "selected": choice,
                        "leaders": context.get("leaders", []),
                    },
                )
            selected = leaders_by_id.get(choice_id or "")
            if selected and selected.get("recommendation") == "reject":
                await self.trace.contradiction(
                    session,
                    code="final_winner_was_rejected",
                    message="Финальный выбор ранее получил рекомендацию reject.",
                    round_id=session.current_round or None,
                    listing_id=choice_id,
                    evidence={"choice": choice_name, "selected": selected},
                )
        session.status, session.stage, session.finished_at, session.lease_expires_at = (
            "completed",
            "completed",
            utcnow(),
            None,
        )
        await self._event(
            session, "completed", "Исследование завершено; сохранено сравнение вариантов."
        )
        await self.db.commit()

    @staticmethod
    def _fallback_comparison(context: dict[str, Any], reason: str) -> dict[str, Any]:
        leaders = context.get("leaders") or []
        best = leaders[0] if leaders else None
        choice = (
            {
                "listing_id": best.get("listing_id"),
                "title": best.get("title"),
                "reason": "Наивысший сохранённый балл; итоговая модель не была вызвана.",
            }
            if best
            else None
        )
        return {
            "conclusion": reason,
            "best_overall": choice,
            "best_budget": choice,
            "best_performance": None,
            "safest": None,
            "requires_clarification": [],
            "avoid": [],
        }

    async def _market_statistics(self, session: ResearchSession) -> dict[str, Any]:
        """Use each durable research item once; do not join historical listing versions."""
        items = list(
            await self.db.scalars(
                select(ResearchItem).where(
                    ResearchItem.session_id == session.id, ResearchItem.status == "complete"
                )
            )
        )
        priced = [
            item
            for item in items
            if item.listing_snapshot.get("price") is not None
            and str(item.listing_snapshot.get("currency") or "").upper() == "UAH"
        ]
        prices = sorted(Decimal(str(item.listing_snapshot["price"])) for item in priced)
        if not prices:
            return {
                "sample_size": 0,
                "currency": "UAH",
                "comparable": False,
                "reason": "Недостаточно подтверждённых цен.",
            }
        middle = len(prices) // 2
        median = prices[middle] if len(prices) % 2 else (prices[middle - 1] + prices[middle]) / 2
        return {
            "sample_size": len(prices),
            "currency": "UAH",
            "min_price": str(prices[0]),
            "median_price": str(median),
            "max_price": str(prices[-1]),
            "comparable": len(prices) >= 3 and bool(session.search_plan.get("model_family")),
            "reason": (
                "Статистика только по оценённым карточкам одного исследования; "
                "сопоставимость конфигураций ограничена описаниями объявлений."
            ),
        }

    @staticmethod
    def _candidate_tokens(candidate: ListingCandidate) -> set[str]:
        values = [candidate.title, candidate.description or "", *candidate.attributes.values()]
        return set(_TOKEN_PATTERN.findall(" ".join(values).casefold()))

    @staticmethod
    def _title_tokens(candidate: ListingCandidate) -> set[str]:
        """Tokens that describe the object being sold, rather than its bundle.

        OLX descriptions routinely mention chargers, batteries and adapters even when
        the advertised object is a laptop.  The title is the cheapest trustworthy
        signal for the primary object; details can later refine an uncertain card.
        """
        return set(_TOKEN_PATTERN.findall(candidate.title.casefold()))

    def _deterministic_relevance(
        self, candidate: ListingCandidate, plan: dict[str, Any]
    ) -> tuple[str, str]:
        """Reject clear mismatches using product semantics, not one arbitrary substring."""
        tokens = self._candidate_tokens(candidate)
        title_tokens = self._title_tokens(candidate)
        max_price = plan.get("max_price")
        if (
            max_price
            and candidate.price is not None
            and (candidate.currency or "").upper() == "UAH"
            and candidate.price > Decimal(str(max_price))
        ):
            return "reject", "Цена выше жёсткого лимита плана."

        product_type = str(plan.get("product_type") or "").casefold()
        product_terms = _PRODUCT_TERMS.get(product_type, {product_type})
        has_product = bool(tokens & product_terms)
        title_has_product = bool(title_tokens & product_terms)
        # An accessory is a mismatch only when it is the primary advertised object.
        # It is safe to mention a charger/battery in a full-device description or
        # title ("ThinkPad T480, зарядка в комплекте").
        title_has_accessory = bool(title_tokens & _ACCESSORY_TERMS)
        if title_has_accessory and not title_has_product:
            return "reject", "Карточка относится к аксессуару, а не к самому товару."
        if tokens & _UNRELATED_PRODUCT_TERMS and not has_product:
            return "reject", "Объявление относится к другому типу товара или услуги."
        if not has_product:
            return "uncertain", "Тип товара не подтверждён карточкой выдачи."

        exclusions = " ".join(
            [*plan.get("hard_requirements", []), *plan.get("excluded_attributes", [])]
        ).casefold()
        if ("не amd" in exclusions or "без amd" in exclusions) and (
            {"amd", "ryzen", "athlon"} & tokens
        ):
            return "reject", "Подтверждена запрещённая AMD-платформа."

        brand = str(plan.get("brand") or "").casefold()
        expected_brand = _BRAND_ALIASES.get(brand, {brand} if brand else set())
        known_other_brand = any(
            aliases & tokens for name, aliases in _BRAND_ALIASES.items() if name != brand
        )
        if expected_brand and not (expected_brand & tokens) and known_other_brand:
            return "reject", "В карточке указан другой обязательный бренд."

        family = str(plan.get("model_family") or "").casefold().replace(" ", "")
        expected_family = _FAMILY_ALIASES.get(family, {family} if family else set())
        has_family = bool(expected_family & tokens)
        if not expected_brand and not expected_family:
            return "accept", "Тип товара подтверждён карточкой."
        if expected_brand and expected_brand & tokens and (not expected_family or has_family):
            return "accept", "Тип товара, бренд и модельное семейство подтверждены."
        if has_family:
            return "accept", "Модельное семейство подтверждено в карточке."
        if expected_brand and expected_brand & tokens:
            return "uncertain", "Бренд подходит, но семейство модели не подтверждено."
        return "uncertain", "Карточка похожа по типу товара, но требует дешёвой проверки."

    @staticmethod
    def _prefilter_rule_code(reason: str) -> str:
        rules = {
            "Цена выше жёсткого лимита плана.": "price_above_hard_limit",
            "Карточка относится к аксессуару, а не к самому товару.": "accessory_only",
            "Объявление относится к другому типу товара или услуги.": "unrelated_product",
            "Тип товара не подтверждён карточкой выдачи.": "product_type_unconfirmed",
            "Подтверждена запрещённая AMD-платформа.": "excluded_platform_amd",
            "В карточке указан другой обязательный бренд.": "required_brand_mismatch",
            "Тип товара подтверждён карточкой.": "product_type_confirmed",
            "Тип товара, бренд и модельное семейство подтверждены.": "brand_family_confirmed",
            "Модельное семейство подтверждено в карточке.": "family_confirmed",
            "Бренд подходит, но семейство модели не подтверждено.": "family_unconfirmed",
            "Карточка похожа по типу товара, но требует дешёвой проверки.": (
                "semantic_check_required"
            ),
            "Не удалось подтвердить полную карточку OLX.": "listing_details_unavailable",
        }
        return rules.get(reason, "unclassified_prefilter_reason")

    async def _reject_before_analysis(
        self, session: ResearchSession, candidate: ListingCandidate, reason: str
    ) -> None:
        session.rejected_before_analysis_count += 1
        await self._event(
            session,
            "prefilter_reject",
            f"Отсеяно до ИИ-анализа: {candidate.title[:120]}",
            {"reason": reason, "url": candidate.canonical_url},
        )
        await self.db.commit()
        logger.info(
            "Candidate rejected before detailed AI analysis",
            extra={
                "research_id": session.id,
                "event": "research_prefilter_reject",
                "status": reason,
            },
        )

    async def _check_uncertain_relevance(
        self,
        session: ResearchSession,
        candidate: ListingCandidate,
        model_info: dict[str, Any],
        reason: str,
    ) -> str:
        """Use a small, bounded Luna call only for cards that rules cannot classify."""
        pricing = model_info.get("pricing") or {}
        prompt_price = AiService._price(pricing.get("prompt"))
        completion_price = AiService._price(pricing.get("completion"))
        request_price = AiService._price(pricing.get("request")) or Decimal("0")
        if prompt_price is None or completion_price is None:
            await self._reject_before_analysis(
                session, candidate, "Неизвестная цена модели для дешёвой проверки."
            )
            return "reject"
        estimate = prompt_price * 2_500 + completion_price * 220 + request_price
        if session.actual_cost_usd + estimate > session.budget_usd:
            session.status, session.stage, session.error_code, session.lease_expires_at = (
                "budget_exhausted",
                "relevance_budget_exhausted",
                "budget_exhausted",
                None,
            )
            await self._event(
                session, "budget", "Лимит расходов исчерпан до проверки неоднозначной карточки."
            )
            await self.db.commit()
            return "reject"
        session.stage = "checking_relevance"
        relevance_input = {
            "plan": session.search_plan,
            "listing": {
                "title": candidate.title,
                "description": candidate.description,
                "attributes": candidate.attributes,
                "price": candidate.price,
                "currency": candidate.currency,
            },
            "prefilter_reason": reason,
            "model": session.model,
        }
        await self.trace.record(
            session,
            stage="relevance_check",
            event_type="relevance_check_started",
            round_id=session.current_round or None,
            listing_id=candidate.source_listing_id,
            payload={"input": relevance_input, "estimated_cost_usd": estimate},
        )
        await self.db.commit()
        started = time.perf_counter()
        try:
            result, usage = await OpenRouterClient(
                self.settings.openrouter_api_key or ""
            ).check_relevance(
                model=session.model or "",
                plan=session.search_plan,
                title=candidate.title,
                description=candidate.description,
                attributes=candidate.attributes,
                price=f"{candidate.price or ''} {candidate.currency or ''}".strip() or None,
            )
        except Exception as exc:
            await self.trace.record(
                session,
                stage="relevance_check",
                event_type="relevance_check_failed",
                round_id=session.current_round or None,
                listing_id=candidate.source_listing_id,
                duration_ms=int((time.perf_counter() - started) * 1000),
                payload={"input": relevance_input, "error_type": type(exc).__name__},
            )
            await self._reject_before_analysis(
                session, candidate, "Дешёвая проверка релевантности не вернула безопасный ответ."
            )
            logger.warning(
                "Cheap relevance check failed",
                extra={
                    "research_id": session.id,
                    "event": "research_relevance_failed",
                    "status": type(exc).__name__,
                },
            )
            return "reject"
        actual = (
            prompt_price * usage["prompt_tokens"]
            + completion_price * usage["completion_tokens"]
            + request_price
        )
        self._record_stage_usage(
            session, "relevance_check", estimate=estimate, actual=actual, usage=usage
        )
        await self.trace.record(
            session,
            stage="relevance_check",
            event_type="relevance_check_completed",
            round_id=session.current_round or None,
            listing_id=candidate.source_listing_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            payload={
                "input": relevance_input,
                "output": result.model_dump(mode="json"),
                "recommendation": result.decision,
                "reason": result.reason,
                "confidence": None,
                "confidence_note": "The relevance schema does not return confidence.",
                "usage": usage,
                "actual_cost_usd": actual,
            },
        )
        session.lease_expires_at = utcnow() + timedelta(minutes=5)
        if result.decision != "accept":
            await self._reject_before_analysis(session, candidate, result.reason)
            return result.decision
        await self._event(
            session,
            "relevance_accepted",
            f"Карточка прошла краткую проверку: {candidate.title[:120]}",
            {"reason": result.reason, "initial_reason": reason},
        )
        await self.db.commit()
        return "accept"

    async def _evaluate_item(
        self,
        session: ResearchSession,
        item: ResearchItem,
        version: ListingVersion,
        model_info: dict[str, Any],
        candidate: ListingCandidate,
    ) -> None:
        prompt_price = AiService._price((model_info.get("pricing") or {}).get("prompt"))
        completion_price = AiService._price((model_info.get("pricing") or {}).get("completion"))
        # The catalog omits ``request`` for models without a fixed request charge.
        pricing = model_info.get("pricing") or {}
        request_price = AiService._price(pricing.get("request")) or Decimal("0")
        if prompt_price is None or completion_price is None:
            session.status, session.stage, session.error_code = (
                "failed",
                "pricing_unknown",
                "pricing_unknown",
            )
            session.lease_expires_at = None
            await self._event(
                session,
                "budget",
                "У модели нет прозрачной цены, исследование безопасно остановлено.",
            )
            await self.db.commit()
            return
        estimate = prompt_price * 5_000 + completion_price * 2_400 + request_price
        if session.actual_cost_usd + estimate > session.budget_usd:
            session.status, session.stage, session.error_code = (
                "budget_exhausted",
                "budget_exhausted",
                "budget_exhausted",
            )
            await self._event(session, "budget", "Лимит расходов исследования исчерпан.")
            await self.db.commit()
            return
        item.status, session.stage = "running", "analyzing_listing"
        analysis_input = {
            "query": session.query,
            "model": session.model,
            "listing": {
                "title": version.title,
                "description": version.description,
                "attributes": version.attributes,
                "price": version.price,
                "currency": version.currency,
                "city": version.city,
                "condition": version.condition,
            },
            "hard_constraints": [
                *session.search_plan.get("hard_requirements", []),
                *session.search_plan.get("excluded_attributes", []),
            ],
            "leaders_context": session.compact_memory,
        }
        await self.trace.record(
            session,
            stage="deep_analysis",
            event_type="deep_analysis_started",
            round_id=session.current_round or None,
            listing_id=item.listing_id,
            payload={"input": analysis_input, "estimated_cost_usd": estimate},
        )
        await self.db.commit()
        logger.info(
            "AI analysis started for listing",
            extra={
                "research_id": session.id,
                "item_id": item.id,
                "event": "research_item_started",
            },
        )
        started = time.perf_counter()
        try:
            result, usage = await OpenRouterClient(
                self.settings.openrouter_api_key or ""
            ).analyze_research_item(
                model=session.model or "",
                query=session.query,
                title=version.title,
                description=version.description,
                attributes=version.attributes,
                price=f"{version.price or ''} {version.currency or ''}".strip() or None,
                compact_memory=session.compact_memory,
            )
        except Exception as exc:
            item.status, item.error_code = "error", "model_request_failed"
            session.cursor += 1
            session.processed_count += 1
            await self._event(
                session,
                "model_error",
                "Не удалось оценить одно объявление; продолжаю со следующим.",
                {"item_id": str(item.id), "type": type(exc).__name__},
            )
            await self.trace.record(
                session,
                stage="deep_analysis",
                event_type="deep_analysis_failed",
                round_id=session.current_round or None,
                listing_id=item.listing_id,
                duration_ms=int((time.perf_counter() - started) * 1000),
                payload={
                    "item_id": item.id,
                    "input": analysis_input,
                    "error_type": type(exc).__name__,
                },
            )
            await self.db.commit()
            logger.warning(
                "AI analysis failed for listing",
                extra={
                    "research_id": session.id,
                    "item_id": item.id,
                    "event": "research_item_failed",
                    "status": type(exc).__name__,
                },
            )
            return
        actual = (
            prompt_price * usage["prompt_tokens"]
            + completion_price * usage["completion_tokens"]
            + request_price
        )
        item.status = "complete"
        item.score = result.score
        item.confidence = Decimal(str(result.confidence))
        item.recommendation = result.recommendation
        item.analysis = result.model_dump(mode="json")
        item.input_tokens = usage["prompt_tokens"]
        item.output_tokens = usage["completion_tokens"]
        item.cached_input_tokens = usage["cached_input_tokens"]
        item.estimated_cost_usd = estimate
        item.actual_cost_usd = actual
        session.cursor += 1
        session.processed_count += 1
        self._record_stage_usage(
            session, "deep_analysis", estimate=estimate, actual=actual, usage=usage
        )
        session.lease_expires_at = utcnow() + timedelta(minutes=5)
        if result.recommendation == "shortlist":
            session.accepted_count += 1
        else:
            session.rejected_count += 1
        session.compact_memory = await self._memory(session)
        result_payload = result.model_dump(mode="json")
        await self.trace.record(
            session,
            stage="deep_analysis",
            event_type="deep_analysis_completed",
            round_id=session.current_round or None,
            listing_id=item.listing_id,
            duration_ms=int((time.perf_counter() - started) * 1000),
            payload={
                "item_id": item.id,
                "input": analysis_input,
                "output": result_payload,
                "score": result.score,
                "recommendation": result.recommendation,
                "confidence": result.confidence,
                "risks": result.risks,
                "evidence": [item.model_dump(mode="json") for item in result.evidence],
                "usage": usage,
                "actual_cost_usd": actual,
            },
        )
        if result.recommendation == "shortlist" and result.score < 40:
            await self.trace.contradiction(
                session,
                code="shortlist_with_low_score",
                message="Положительная рекомендация выдана объявлению с низким score.",
                listing_id=item.listing_id,
                round_id=session.current_round or None,
                evidence={"score": result.score, "recommendation": result.recommendation},
            )
        hard_decision, hard_reason = self._deterministic_relevance(candidate, session.search_plan)
        if result.recommendation == "shortlist" and hard_decision == "reject":
            await self.trace.contradiction(
                session,
                code="positive_recommendation_violates_hard_constraint",
                message="Положительная рекомендация конфликтует с детерминированным ограничением.",
                listing_id=item.listing_id,
                round_id=session.current_round or None,
                evidence={
                    "recommendation": result.recommendation,
                    "score": result.score,
                    "constraint_reason": hard_reason,
                    "rule": self._prefilter_rule_code(hard_reason),
                },
            )
        await self.trace.record(
            session,
            stage="persistence",
            event_type="item_checkpoint_saved",
            round_id=session.current_round or None,
            listing_id=item.listing_id,
            payload={
                "item_id": item.id,
                "status": item.status,
                "sequence": item.sequence,
                "cursor": session.cursor,
                "processed_count": session.processed_count,
            },
        )
        await self._event(
            session,
            "evaluated",
            f"Оценено: {version.title[:120]}",
            {"item_id": str(item.id), "score": result.score},
        )
        await self.db.commit()
        logger.info(
            "AI analysis saved for listing",
            extra={
                "research_id": session.id,
                "item_id": item.id,
                "event": "research_item_completed",
                "status": f"score={result.score}; recommendation={result.recommendation}",
            },
        )

    async def _memory(self, session: ResearchSession) -> dict[str, Any]:
        top = (
            await self.db.execute(
                select(
                    ResearchItem,
                    ListingVersion.title,
                    ListingVersion.price,
                    ListingVersion.currency,
                )
                .join(ListingVersion, ListingVersion.listing_id == ResearchItem.listing_id)
                .where(ResearchItem.session_id == session.id, ResearchItem.status == "complete")
                .order_by(ResearchItem.score.desc())
                .limit(5)
            )
        ).all()
        leaders = []
        for item, title, price, currency in top:
            leaders.append(
                {
                    "title": title[:160],
                    "score": item.score,
                    "price": str(price) if price is not None else None,
                    "currency": currency,
                    "summary": item.analysis.get("summary", "")[:240],
                }
            )
        return {"leaders": leaders, "market_notes": []}

    async def _event(
        self,
        session: ResearchSession,
        kind: str,
        message: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            ResearchEvent(session_id=session.id, kind=kind, message=message, payload=payload or {})
        )
        await self.trace.record(
            session,
            stage=session.stage or "lifecycle",
            event_type=f"research_event.{kind}",
            round_id=session.current_round or None,
            payload={"message": message, "details": payload or {}},
        )

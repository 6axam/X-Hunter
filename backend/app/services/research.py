from __future__ import annotations

import re
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
        await self._event(session, "collecting", "Ищу подходящие объявления на OLX.ua.")
        await self.db.commit()
        candidates = await self._collect_olx(session)
        if candidates is None:
            logger.warning(
                "OLX collection failed",
                extra={"research_id": session.id, "event": "research_collection_failed"},
            )
            return session.status
        logger.info(
            "OLX candidates collected",
            extra={
                "research_id": session.id,
                "event": "research_candidates_collected",
                "status": f"count={len(candidates)}",
            },
        )

        for candidate in candidates:
            await self.db.refresh(session)
            if session.status != "running":
                return session.status
            if session.processed_count >= session.max_items:
                break
            decision, reason = self._deterministic_relevance(candidate, session.search_plan)
            if decision == "reject":
                await self._reject_before_analysis(session, candidate, reason)
                continue
            details = await self.source.fetch_listing(candidate.canonical_url)
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
            await self._evaluate_item(session, item, version, model_info)

        if session.status == "running":
            session.status, session.stage, session.finished_at, session.lease_expires_at = (
                "completed",
                "completed",
                utcnow(),
                None,
            )
            await self._event(session, "completed", "Исследование завершено. Рейтинг сохранён.")
            await self.db.commit()
        logger.info(
            "Research session ended",
            extra={
                "research_id": session.id,
                "event": "research_finished",
                "status": session.status,
            },
        )
        return session.status

    async def _ensure_search_plan(
        self, session: ResearchSession, model_info: dict[str, Any]
    ) -> bool:
        """Create exactly one durable plan before any marketplace URL is built."""
        if session.search_plan:
            return True
        pricing = model_info.get("pricing") or {}
        prompt_price = AiService._price(pricing.get("prompt"))
        completion_price = AiService._price(pricing.get("completion"))
        request_price = AiService._price(pricing.get("request")) or Decimal("0")
        if prompt_price is None or completion_price is None:
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
        await self.db.commit()
        try:
            client = OpenRouterClient(self.settings.openrouter_api_key or "")
            plan, usage = await client.plan_search(model=session.model or "", query=session.query)
        except Exception as exc:
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
        session.search_plan = plan.model_dump(mode="json")
        session.planning_estimated_cost_usd = estimate
        session.planning_actual_cost_usd = actual
        session.planning_input_tokens = usage["prompt_tokens"]
        session.planning_output_tokens = usage["completion_tokens"]
        session.estimated_cost_usd += estimate
        session.actual_cost_usd += actual
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

    async def _collect_olx(self, session: ResearchSession) -> list[ListingCandidate] | None:
        queries = list(dict.fromkeys(session.search_plan.get("search_queries") or []))
        if not queries:
            session.status, session.stage, session.error_code, session.lease_expires_at = (
                "failed",
                "planning_invalid",
                "planning_invalid",
                None,
            )
            await self._event(session, "planning_error", "План не содержит поисковых фраз для OLX.")
            await self.db.commit()
            return None

        candidates: list[ListingCandidate] = []
        seen: set[str] = set()
        failures: list[dict[str, str]] = []
        # Each planned phrase gets its first page.  Only later pagination is bounded by
        # the global limit, otherwise a broad first phrase can hide model-family variants.
        max_candidates = max(session.max_items * 5, 25) * len(queries)
        for phrase in queries:
            specification = self._search_spec(phrase, session.search_plan)
            if specification is None:
                failures.append({"query": phrase, "code": "query_build_failed"})
                continue
            search_record: dict[str, Any] = {
                "query": phrase,
                "url": specification.current_url,
                "price_to": str(specification.price_to) if specification.price_to else None,
                "pages": [],
            }
            for page in range(1, self.settings.scan_max_pages + 1):
                if page > 1 and len(candidates) >= max_candidates:
                    break
                result = await self.source.fetch_search_page(specification, page)
                page_record = {
                    "page": page,
                    "state": result.state.value,
                    "url": result.metadata.get("requested_url", specification.current_url),
                    "final_url": result.metadata.get("final_url"),
                    "results": len(result.data or []),
                    "error_code": result.error_code,
                }
                search_record["pages"].append(page_record)
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
                        continue
                    seen.add(identifier)
                    candidates.append(candidate)
                if len(page_candidates) == 0:
                    break
            session.executed_searches = [*session.executed_searches, search_record]
            await self.db.commit()

        session.collected_count += len(candidates)
        if not candidates:
            session.status, session.stage, session.error_code, session.lease_expires_at = (
                "failed",
                "source_unavailable",
                "search_no_verified_results",
                None,
            )
            await self._event(
                session,
                "source_error",
                "OLX не подтвердил релевантную выдачу ни для одной поисковой фразы.",
                {"failures": failures},
            )
            await self.db.commit()
            return None
        await self._event(
            session,
            "collected",
            f"Подтверждено кандидатов OLX: {len(candidates)}.",
            {"queries": queries, "collected": len(candidates), "failures": failures},
        )
        await self.db.commit()
        return candidates

    @staticmethod
    def _candidate_tokens(candidate: ListingCandidate) -> set[str]:
        values = [candidate.title, candidate.description or "", *candidate.attributes.values()]
        return set(_TOKEN_PATTERN.findall(" ".join(values).casefold()))

    def _deterministic_relevance(
        self, candidate: ListingCandidate, plan: dict[str, Any]
    ) -> tuple[str, str]:
        """Reject clear mismatches using product semantics, not one arbitrary substring."""
        tokens = self._candidate_tokens(candidate)
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
        # Full OLX descriptions often mention "ноутбук" while advertising only a
        # charger or power supply.  Such accessories are never the requested device.
        if tokens & _ACCESSORY_TERMS:
            return "reject", "Карточка относится к аксессуару, а не к самому товару."
        if tokens & _UNRELATED_PRODUCT_TERMS and not has_product:
            return "reject", "Объявление относится к другому типу товара или услуги."
        if not has_product:
            return "uncertain", "Тип товара не подтверждён карточкой выдачи."

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
        if expected_brand and expected_brand & tokens and (not expected_family or has_family):
            return "accept", "Тип товара, бренд и модельное семейство подтверждены."
        if has_family:
            return "accept", "Модельное семейство подтверждено в карточке."
        if expected_brand and expected_brand & tokens:
            return "uncertain", "Бренд подходит, но семейство модели не подтверждено."
        return "uncertain", "Карточка похожа по типу товара, но требует дешёвой проверки."

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
        await self.db.commit()
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
        session.estimated_cost_usd += estimate
        session.actual_cost_usd += actual
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
        await self.db.commit()
        logger.info(
            "AI analysis started for listing",
            extra={
                "research_id": session.id,
                "item_id": item.id,
                "event": "research_item_started",
            },
        )
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
        session.estimated_cost_usd += estimate
        session.actual_cost_usd += actual
        session.lease_expires_at = utcnow() + timedelta(minutes=5)
        if result.recommendation == "shortlist":
            session.accepted_count += 1
        else:
            session.rejected_count += 1
        session.compact_memory = await self._memory(session)
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

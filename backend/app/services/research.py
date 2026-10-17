from __future__ import annotations

# ruff: noqa: E501
from decimal import Decimal
from typing import Any
from urllib.parse import quote
from uuid import UUID

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.openrouter import OpenRouterClient
from app.api.errors import AppError
from app.config import Settings
from app.models import ListingVersion, ResearchEvent, ResearchItem, ResearchSession, User
from app.models.base import utcnow
from app.services.ai import AiService
from app.services.scanning import ScanService
from app.sources.base import SourceState
from app.sources.olx import OlxSource
from app.sources.types import ListingCandidate

MARKETPLACES = {
    "olx": {"label": "OLX.ua", "available": True, "reason": None},
    "prom": {"label": "Prom.ua", "available": False, "reason": "Адаптер ещё не проверен"},
    "rozetka": {"label": "Rozetka", "available": False, "reason": "Адаптер ещё не проверен"},
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
            session.status, session.stage = "paused", "paused"
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
            session.status, session.stage, session.finished_at = "cancelled", "cancelled", utcnow()
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
        session = await self.db.get(ResearchSession, session_id)
        if not session or session.status not in {"queued", "running"}:
            return "skipped"
        if not self.settings.openrouter_api_key or not session.model:
            session.status, session.stage = "waiting_configuration", "waiting_for_openrouter"
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
            await self._event(
                session,
                "configuration",
                "Выбранная модель не найдена в каталоге OpenRouter этого ключа.",
                {"model": session.model},
            )
            await self.db.commit()
            return session.status

        session.status, session.stage, session.started_at = (
            "running",
            "collecting_olx",
            session.started_at or utcnow(),
        )
        await self._event(session, "collecting", "Ищу подходящие объявления на OLX.ua.")
        await self.db.commit()
        candidates = await self._collect_olx(session)
        if candidates is None:
            return session.status

        for candidate in candidates:
            await self.db.refresh(session)
            if session.status != "running":
                return session.status
            if session.processed_count >= session.max_items:
                break
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
            session.status, session.stage, session.finished_at = "completed", "completed", utcnow()
            await self._event(session, "completed", "Исследование завершено. Рейтинг сохранён.")
            await self.db.commit()
        return session.status

    async def _collect_olx(self, session: ResearchSession) -> list[ListingCandidate] | None:
        url = f"https://www.olx.ua/uk/list/q-{quote(session.query, safe='')}/"
        parsed = self.source.parse_search_url(url)
        if not parsed.search:
            session.status, session.stage, session.error_code = (
                "failed",
                "source_error",
                "query_build_failed",
            )
            await self.db.commit()
            return None
        result = await self.source.fetch_search_page(parsed.search)
        if result.state not in {SourceState.COMPLETE, SourceState.PARTIAL}:
            session.status, session.stage, session.error_code = (
                "failed",
                "source_unavailable",
                result.error_code,
            )
            await self._event(
                session,
                "source_error",
                result.message or "OLX временно недоступен.",
                {"code": result.error_code},
            )
            await self.db.commit()
            return None
        candidates: list[ListingCandidate] = []
        for raw in (result.data or [])[: session.max_items]:
            candidate = ListingCandidate.model_validate(raw)
            details = await self.source.fetch_listing(candidate.canonical_url)
            if details.state is SourceState.COMPLETE and details.data:
                candidate = ListingCandidate.model_validate(details.data)
            candidates.append(candidate)
        return candidates

    async def _evaluate_item(
        self,
        session: ResearchSession,
        item: ResearchItem,
        version: ListingVersion,
        model_info: dict[str, Any],
    ) -> None:
        prompt_price = AiService._price((model_info.get("pricing") or {}).get("prompt"))
        completion_price = AiService._price((model_info.get("pricing") or {}).get("completion"))
        request_price = AiService._price((model_info.get("pricing") or {}).get("request"))
        if prompt_price is None or completion_price is None or request_price is None:
            session.status, session.stage, session.error_code = (
                "failed",
                "pricing_unknown",
                "pricing_unknown",
            )
            await self._event(
                session,
                "budget",
                "У модели нет прозрачной цены, исследование безопасно остановлено.",
            )
            await self.db.commit()
            return
        estimate = prompt_price * 5_000 + completion_price * 900 + request_price
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

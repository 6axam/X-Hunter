from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import AppError
from app.api.schemas import SearchDraftPatch, SearchPatchRequest
from app.models import Search, SearchDraft, User
from app.models.base import as_utc, utcnow
from app.sources.olx import OlxPageType, OlxSource


class SearchService:
    def __init__(self, db: AsyncSession, source: OlxSource | None = None) -> None:
        self.db = db
        self.source = source or OlxSource()

    async def import_url(self, owner: User, url: str) -> SearchDraft:
        parsed = self.source.parse_search_url(url)
        if parsed.page_type is OlxPageType.SEARCH:
            assert parsed.search is not None
            payload = parsed.search.model_dump(mode="json")
            path_name = parsed.search.path.strip("/").split("/")[-1] or "OLX"
            name = path_name.replace("-", " ").strip().title()[:160] or "Новый поиск"
            opaque = parsed.search.opaque_query
        else:
            payload = {"listing_url": parsed.listing_url}
            name = "Сохранённое объявление"
            opaque = {}
        draft = SearchDraft(
            owner_id=owner.id,
            original_url=url,
            page_type=parsed.page_type.value,
            name=name,
            parsed_spec=payload,
            opaque_parameters=opaque,
            rules={"required_groups": [], "excluded": []},
            ai_settings={"enabled": False},
            expires_at=utcnow() + timedelta(days=7),
        )
        self.db.add(draft)
        await self.db.commit()
        await self.db.refresh(draft)
        return draft

    async def get_draft(self, owner: User, draft_id: UUID) -> SearchDraft:
        draft = await self.db.scalar(
            select(SearchDraft).where(
                SearchDraft.id == draft_id,
                SearchDraft.owner_id == owner.id,
            )
        )
        if draft is None:
            raise AppError(status_code=404, code="draft_not_found", message="Черновик не найден.")
        if as_utc(draft.expires_at) < utcnow():
            raise AppError(status_code=404, code="draft_expired", message="Черновик устарел.")
        return draft

    async def patch_draft(
        self, owner: User, draft_id: UUID, patch: SearchDraftPatch
    ) -> SearchDraft:
        draft = await self.get_draft(owner, draft_id)
        changes = patch.model_dump(exclude_unset=True)
        for field, value in changes.items():
            setattr(draft, field, value)
        await self.db.commit()
        await self.db.refresh(draft)
        return draft

    async def create_search(
        self,
        owner: User,
        draft: SearchDraft,
        *,
        active: bool,
        interval_minutes: int,
    ) -> Search:
        if draft.page_type != OlxPageType.SEARCH.value:
            raise AppError(
                status_code=422,
                code="listing_draft_not_search",
                message="Ссылка отдельного объявления не запускает мониторинг выдачи.",
            )
        current_url = str(draft.parsed_spec.get("current_url") or "")
        if not current_url:
            raise AppError(
                status_code=422,
                code="draft_incomplete",
                message="В черновике отсутствует собранная ссылка OLX.",
            )
        search = Search(
            owner_id=owner.id,
            name=draft.name,
            original_url=draft.original_url,
            current_url=current_url,
            specification=draft.parsed_spec,
            opaque_parameters=draft.opaque_parameters,
            rules=draft.rules,
            ai_settings=draft.ai_settings,
            event_settings={"price_drop_percent": 5},
            active=active,
            interval_minutes=interval_minutes,
            next_run_at=utcnow() if active else None,
        )
        self.db.add(search)
        await self.db.delete(draft)
        await self.db.commit()
        await self.db.refresh(search)
        return search

    async def list_searches(self, owner: User) -> list[Search]:
        result = await self.db.scalars(
            select(Search).where(Search.owner_id == owner.id).order_by(Search.created_at.desc())
        )
        return list(result)

    async def get_search(self, owner: User, search_id: UUID) -> Search:
        search = await self.db.scalar(
            select(Search).where(Search.id == search_id, Search.owner_id == owner.id)
        )
        if search is None:
            raise AppError(status_code=404, code="search_not_found", message="Поиск не найден.")
        return search

    async def patch_search(self, owner: User, search_id: UUID, patch: SearchPatchRequest) -> Search:
        search = await self.get_search(owner, search_id)
        if search.revision != patch.expected_revision:
            raise AppError(
                status_code=409,
                code="revision_conflict",
                message="Настройки поиска уже изменились. Обновите данные и повторите действие.",
            )
        changes = patch.model_dump(exclude_unset=True, exclude={"expected_revision"})
        semantic_fields = {"rules", "ai_settings", "interval_minutes"}
        for field, value in changes.items():
            setattr(search, field, value)
        if semantic_fields.intersection(changes):
            search.revision += 1
        if "active" in changes:
            search.next_run_at = utcnow() if search.active else None
        await self.db.commit()
        await self.db.refresh(search)
        return search

    async def delete_search(self, owner: User, search_id: UUID) -> None:
        search = await self.get_search(owner, search_id)
        await self.db.delete(search)
        await self.db.commit()

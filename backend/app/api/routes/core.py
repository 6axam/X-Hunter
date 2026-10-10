from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import FileResponse

from app.api.dependencies import CurrentUser, DatabaseSession
from app.api.schemas import (
    FilterSchemaResponse,
    FindingResponse,
    FindingStatePatch,
    FindingStateResponse,
    MeResponse,
    ScanRunResponse,
    SearchCreateRequest,
    SearchDraftPatch,
    SearchDraftResponse,
    SearchImportRequest,
    SearchPatchRequest,
    SearchResponse,
)
from app.config import Settings, get_settings
from app.models import Media
from app.services.findings import FindingService
from app.services.media import owner_can_access_media
from app.services.runs import RunService
from app.services.searches import SearchService
from app.sources.olx import OlxSource

router = APIRouter()


def search_service(db: DatabaseSession, settings: Annotated[Settings, Depends(get_settings)]):
    return SearchService(db, OlxSource(http_enabled=settings.source_http_enabled))


Searches = Annotated[SearchService, Depends(search_service)]


@router.get("/me", response_model=MeResponse, tags=["owner"])
async def me(user: CurrentUser, settings: Annotated[Settings, Depends(get_settings)]) -> MeResponse:
    return MeResponse(
        telegram_id=user.telegram_id,
        timezone=user.timezone,
        capabilities={
            "telegram_configured": bool(settings.telegram_bot_token),
            "openrouter_configured": bool(settings.openrouter_api_key),
            "source_http_enabled": settings.source_http_enabled,
        },
    )


@router.get("/filter-schemas", response_model=FilterSchemaResponse, tags=["searches"])
async def filter_schemas(_: CurrentUser) -> FilterSchemaResponse:
    return FilterSchemaResponse(
        fields=[
            {"key": "currency", "label": "Валюта", "type": "enum", "values": ["UAH"]},
            {"key": "price_from", "label": "Цена от", "type": "decimal"},
            {"key": "price_to", "label": "Цена до", "type": "decimal"},
            {
                "key": "seller_type",
                "label": "Продавец",
                "type": "enum",
                "values": ["private", "business"],
            },
            {
                "key": "order",
                "label": "Сортировка",
                "type": "enum",
                "values": ["created_at:desc", "filter_float_price:asc", "filter_float_price:desc"],
            },
        ]
    )


@router.post(
    "/search-imports",
    response_model=SearchDraftResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["searches"],
)
async def import_search(
    body: SearchImportRequest, user: CurrentUser, searches: Searches
) -> SearchDraftResponse:
    return SearchDraftResponse.model_validate(await searches.import_url(user, body.url))


@router.get("/search-drafts/{draft_id}", response_model=SearchDraftResponse, tags=["searches"])
async def get_search_draft(
    draft_id: UUID, user: CurrentUser, searches: Searches
) -> SearchDraftResponse:
    return SearchDraftResponse.model_validate(await searches.get_draft(user, draft_id))


@router.patch("/search-drafts/{draft_id}", response_model=SearchDraftResponse, tags=["searches"])
async def patch_search_draft(
    draft_id: UUID,
    body: SearchDraftPatch,
    user: CurrentUser,
    searches: Searches,
) -> SearchDraftResponse:
    return SearchDraftResponse.model_validate(await searches.patch_draft(user, draft_id, body))


@router.get("/searches", response_model=list[SearchResponse], tags=["searches"])
async def list_searches(user: CurrentUser, searches: Searches) -> list[SearchResponse]:
    return [SearchResponse.model_validate(item) for item in await searches.list_searches(user)]


@router.post(
    "/searches",
    response_model=SearchResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["searches"],
)
async def create_search(
    body: SearchCreateRequest, user: CurrentUser, searches: Searches
) -> SearchResponse:
    draft = await searches.get_draft(user, body.draft_id)
    result = await searches.create_search(
        user,
        draft,
        active=body.active,
        interval_minutes=body.interval_minutes,
    )
    return SearchResponse.model_validate(result)


@router.get("/searches/{search_id}", response_model=SearchResponse, tags=["searches"])
async def get_search(search_id: UUID, user: CurrentUser, searches: Searches) -> SearchResponse:
    return SearchResponse.model_validate(await searches.get_search(user, search_id))


@router.patch("/searches/{search_id}", response_model=SearchResponse, tags=["searches"])
async def patch_search(
    search_id: UUID,
    body: SearchPatchRequest,
    user: CurrentUser,
    searches: Searches,
) -> SearchResponse:
    return SearchResponse.model_validate(await searches.patch_search(user, search_id, body))


@router.delete("/searches/{search_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["searches"])
async def delete_search(search_id: UUID, user: CurrentUser, searches: Searches) -> Response:
    await searches.delete_search(user, search_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/searches/{search_id}/scans", response_model=ScanRunResponse, tags=["searches"])
async def start_scan(
    search_id: UUID,
    user: CurrentUser,
    searches: Searches,
    db: DatabaseSession,
    settings: Annotated[Settings, Depends(get_settings)],
) -> ScanRunResponse:
    search = await searches.get_search(user, search_id)
    run = await RunService(db, OlxSource(http_enabled=settings.source_http_enabled)).start(search)
    return ScanRunResponse.model_validate(run)


@router.get("/findings", response_model=list[FindingResponse], tags=["findings"])
async def list_findings(
    user: CurrentUser,
    db: DatabaseSession,
    saved: bool | None = Query(default=None),
    finding_status: str | None = Query(default="all", alias="status"),
) -> list[FindingResponse]:
    items = await FindingService(db).list(user, saved=saved, status=finding_status)
    return [FindingResponse.model_validate(item) for item in items]


@router.get("/findings/{listing_id}", response_model=FindingResponse, tags=["findings"])
async def get_finding(listing_id: UUID, user: CurrentUser, db: DatabaseSession) -> FindingResponse:
    return FindingResponse.model_validate(await FindingService(db).get(user, listing_id))


@router.patch(
    "/findings/{listing_id}/state",
    response_model=FindingStateResponse,
    tags=["findings"],
)
async def patch_finding_state(
    listing_id: UUID,
    body: FindingStatePatch,
    user: CurrentUser,
    db: DatabaseSession,
) -> FindingStateResponse:
    changes = body.model_dump(exclude_unset=True)
    result = await FindingService(db).patch_state(user, listing_id, changes)
    return FindingStateResponse.model_validate(result)


@router.get("/media/{media_id}", response_class=FileResponse, tags=["media"])
async def get_media(
    media_id: UUID,
    user: CurrentUser,
    db: DatabaseSession,
) -> FileResponse:
    media = await db.get(Media, media_id)
    if (
        media is None
        or media.status != "ready"
        or not media.safe_path
        or not await owner_can_access_media(db, user, media)
    ):
        from app.api.errors import AppError

        raise AppError(status_code=404, code="media_not_found", message="Изображение не найдено.")
    return FileResponse(media.safe_path, media_type=media.mime_type or "image/jpeg")

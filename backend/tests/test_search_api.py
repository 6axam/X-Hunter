from __future__ import annotations

from typing import Any

import httpx
import pytest


@pytest.mark.asyncio
async def test_import_edit_create_and_revision_conflict(api_client: httpx.AsyncClient) -> None:
    imported = await api_client.post(
        "/api/v1/search-imports",
        json={
            "url": (
                "https://www.olx.ua/uk/elektronika/?currency=UAH"
                "&search%5Bfilter_float_price%3Ato%5D=12000"
            )
        },
    )
    assert imported.status_code == 201, imported.text
    draft = imported.json()
    assert draft["page_type"] == "search"
    assert draft["parsed_spec"]["price_to"] == "12000"

    edited = await api_client.patch(
        f"/api/v1/search-drafts/{draft['id']}",
        json={
            "name": "Steam Deck до 12 000",
            "rules": {
                "required_groups": [{"values": ["steam deck", "стим дек"], "scope": "both"}],
                "excluded": [{"value": "чехол", "scope": "both"}],
            },
        },
    )
    assert edited.status_code == 200
    assert edited.json()["name"] == "Steam Deck до 12 000"

    created = await api_client.post(
        "/api/v1/searches",
        json={"draft_id": draft["id"], "active": True, "interval_minutes": 15},
    )
    assert created.status_code == 201, created.text
    search = created.json()
    assert search["active"] is True
    assert search["revision"] == 1
    assert search["next_run_at"] is not None

    changed = await api_client.patch(
        f"/api/v1/searches/{search['id']}",
        json={"expected_revision": 1, "rules": {"required_groups": [], "excluded": []}},
    )
    assert changed.status_code == 200
    assert changed.json()["revision"] == 2

    stale = await api_client.patch(
        f"/api/v1/searches/{search['id']}",
        json={"expected_revision": 1, "name": "Устаревшая запись"},
    )
    assert stale.status_code == 409
    assert stale.json()["code"] == "revision_conflict"


@pytest.mark.asyncio
async def test_owner_is_not_taken_from_arbitrary_first_visitor(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.get("/api/v1/me", headers={"X-Dev-Telegram-Id": "222"})
    assert response.status_code == 403
    assert response.json()["code"] == "access_denied"


@pytest.mark.asyncio
async def test_listing_url_creates_non_monitoring_draft(api_client: httpx.AsyncClient) -> None:
    imported = await api_client.post(
        "/api/v1/search-imports",
        json={"url": "https://www.olx.ua/d/uk/obyavlenie/test-IDabc.html"},
    )
    assert imported.status_code == 201
    draft: dict[str, Any] = imported.json()
    assert draft["page_type"] == "listing"

    created = await api_client.post(
        "/api/v1/searches",
        json={"draft_id": draft["id"], "active": True, "interval_minutes": 15},
    )
    assert created.status_code == 422
    assert created.json()["code"] == "listing_draft_not_search"

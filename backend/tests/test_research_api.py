import io
import zipfile

import httpx
import pytest


@pytest.mark.asyncio
async def test_research_session_response_serializes_decimal_costs(
    api_client: httpx.AsyncClient,
) -> None:
    response = await api_client.post(
        "/api/v1/research-sessions",
        json={"query": "електровелосипед", "marketplaces": ["olx"], "budget_usd": "0.10"},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "waiting_configuration"
    assert payload["budget_usd"] == "0.100000"


@pytest.mark.asyncio
async def test_research_diagnostics_endpoint_downloads_private_zip(
    api_client: httpx.AsyncClient,
) -> None:
    created = await api_client.post(
        "/api/v1/research-sessions",
        json={"query": "mini PC", "marketplaces": ["olx"], "budget_usd": "0.10"},
    )
    research_id = created.json()["id"]

    response = await api_client.get(f"/api/v1/research-sessions/{research_id}/diagnostics.zip")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["cache-control"] == "private, no-store"
    assert "attachment" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            "trace.jsonl",
            "summary.json",
            "manifest.json",
            "report.md",
        }

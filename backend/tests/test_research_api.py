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

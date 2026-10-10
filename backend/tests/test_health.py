import httpx
import pytest

from app.config import Settings, get_settings
from app.main import app


@pytest.mark.asyncio
async def test_health_is_honest_about_disabled_source() -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        source_http_enabled=False, source_browser_enabled=False
    )
    transport = httpx.ASGITransport(app=app)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/health")
    finally:
        app.dependency_overrides.pop(get_settings, None)

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["source_http_enabled"] is False
    assert response.headers["x-request-id"]

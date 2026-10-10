from fastapi.testclient import TestClient

from app.main import app


def test_health_is_honest_about_disabled_source() -> None:
    response = TestClient(app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["source_http_enabled"] is False
    assert response.headers["x-request-id"]

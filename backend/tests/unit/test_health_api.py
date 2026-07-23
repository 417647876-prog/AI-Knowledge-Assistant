import inspect

from fastapi.testclient import TestClient

from app.api.v1.health import health
from app.main import create_app


def test_health_returns_application_status() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "AI 企业知识库助手",
    }


def test_health_is_a_process_liveness_endpoint() -> None:
    assert list(inspect.signature(health).parameters) == []

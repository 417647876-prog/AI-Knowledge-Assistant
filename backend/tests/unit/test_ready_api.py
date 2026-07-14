from fastapi.testclient import TestClient

from app.api.dependencies import database_is_ready
from app.main import create_app


async def ready_database() -> bool:
    return True


async def unavailable_database() -> bool:
    return False


def test_ready_returns_ok_when_database_and_pgvector_are_ready() -> None:
    app = create_app()
    app.dependency_overrides[database_is_ready] = ready_database

    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_returns_503_when_database_is_unavailable() -> None:
    app = create_app()
    app.dependency_overrides[database_is_ready] = unavailable_database

    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"

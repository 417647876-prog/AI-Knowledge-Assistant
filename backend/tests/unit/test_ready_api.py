from fastapi.testclient import TestClient

from app.api.dependencies import database_is_ready
from app.api.v1.health import migrations_are_current
from app.main import create_app


async def ready_database() -> bool:
    return True


async def unavailable_database() -> bool:
    return False


async def current_migrations() -> bool:
    return True


async def outdated_migrations() -> bool:
    return False


def test_ready_returns_ok_when_database_and_pgvector_are_ready() -> None:
    app = create_app()
    app.dependency_overrides[database_is_ready] = ready_database
    app.dependency_overrides[migrations_are_current] = current_migrations

    response = TestClient(app).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_returns_503_when_database_is_unavailable() -> None:
    app = create_app()
    app.dependency_overrides[database_is_ready] = unavailable_database
    app.dependency_overrides[migrations_are_current] = current_migrations

    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DATABASE_UNAVAILABLE"


def test_ready_returns_503_when_migrations_are_not_current() -> None:
    app = create_app()
    app.dependency_overrides[database_is_ready] = ready_database
    app.dependency_overrides[migrations_are_current] = outdated_migrations

    response = TestClient(app).get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MIGRATIONS_OUTDATED"

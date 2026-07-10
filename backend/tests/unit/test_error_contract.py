from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.exceptions import AppError
from app.main import create_app


def test_app_error_uses_request_id_and_safe_envelope() -> None:
    app = create_app()
    router = APIRouter()

    @router.get("/_test/error")
    async def raise_error() -> None:
        raise AppError(
            code="DOCUMENT_NOT_FOUND",
            message="文档不存在。",
            status_code=404,
        )

    app.include_router(router)
    client = TestClient(app)

    response = client.get(
        "/_test/error",
        headers={"X-Request-ID": "test-request-001"},
    )

    assert response.status_code == 404
    assert response.headers["X-Request-ID"] == "test-request-001"
    assert response.json() == {
        "error": {
            "code": "DOCUMENT_NOT_FOUND",
            "message": "文档不存在。",
            "request_id": "test-request-001",
        }
    }

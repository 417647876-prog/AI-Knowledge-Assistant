import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse

from app.api.middleware import RequestIdMiddleware
from app.core.request_context import get_request_id


@pytest.mark.asyncio
async def test_request_id_middleware_binds_header_to_core_context() -> None:
    app = FastAPI()

    @app.get("/request-context")
    async def read_request_context() -> dict[str, str]:
        return {"request_id": get_request_id()}

    app.add_middleware(RequestIdMiddleware)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/request-context",
            headers={"X-Request-ID": "context-request-001"},
        )

    assert response.status_code == 200
    assert response.json() == {"request_id": "context-request-001"}
    assert response.headers["X-Request-ID"] == "context-request-001"
    assert get_request_id() == ""


@pytest.mark.asyncio
async def test_request_id_context_remains_available_while_streaming() -> None:
    app = FastAPI()

    @app.get("/stream-context")
    async def stream_request_context() -> StreamingResponse:
        async def source():
            yield get_request_id()

        return StreamingResponse(source(), media_type="text/plain")

    app.add_middleware(RequestIdMiddleware)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/stream-context",
            headers={"X-Request-ID": "stream-context-001"},
        )

    assert response.status_code == 200
    assert response.text == "stream-context-001"
    assert get_request_id() == ""

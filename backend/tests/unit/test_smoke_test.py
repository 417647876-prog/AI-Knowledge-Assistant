from collections.abc import Iterator

import httpx
import pytest

from scripts.smoke_test import SmokeTestError, run_smoke_test, wait_for_document_ready


def _responses(statuses: list[str]) -> httpx.MockTransport:
    values: Iterator[str] = iter(statuses)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": next(values)})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_wait_for_document_ready_returns_when_document_is_ready() -> None:
    async with httpx.AsyncClient(transport=_responses(["pending", "ready"])) as client:
        payload = await wait_for_document_ready(
            client, "http://testserver", "document-id", timeout_seconds=1, poll_interval_seconds=0
        )

    assert payload["status"] == "ready"


@pytest.mark.asyncio
async def test_wait_for_document_ready_raises_with_processing_error() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={"status": "failed", "error_message": "无法解析文档"},
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(SmokeTestError, match="无法解析文档"):
            await wait_for_document_ready(
                client,
                "http://testserver",
                "document-id",
                timeout_seconds=1,
                poll_interval_seconds=0,
            )


@pytest.mark.asyncio
async def test_run_smoke_test_authenticates_and_uses_fixed_request_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths: list[str] = []
    access_token = "sensitive-access-token"
    password = "sensitive-password-123"

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/ready":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/api/v1/auth/login":
            assert request.headers.get("authorization") is None
            assert request.read().decode() == (
                '{"username":"admin","password":"sensitive-password-123"}'
            )
            return httpx.Response(
                200,
                headers={
                    "set-cookie": (
                        "refresh_token=sensitive-refresh-token; HttpOnly; "
                        "Path=/api/v1/auth; SameSite=lax"
                    )
                },
                json={
                    "access_token": access_token,
                    "token_type": "bearer",
                    "expires_in": 900,
                    "user": {
                        "id": "user-id",
                        "username": "admin",
                        "role": "admin",
                        "is_active": True,
                    },
                },
            )

        assert request.headers["authorization"] == f"Bearer {access_token}"
        if request.url.path == "/api/v1/auth/me":
            assert request.headers["cookie"] == "refresh_token=sensitive-refresh-token"
            return httpx.Response(
                200,
                json={
                    "id": "user-id",
                    "username": "admin",
                    "role": "admin",
                    "is_active": True,
                },
            )
        if request.url.path == "/api/v1/knowledge-bases":
            return httpx.Response(201, json={"id": "knowledge-base-id"})
        if request.url.path.endswith("/documents"):
            return httpx.Response(202, json={"document_id": "document-id"})
        if request.url.path == "/api/v1/documents/document-id":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path.endswith("/questions"):
            return httpx.Response(
                200,
                json={"citations": [{"file_name": "annual-leave.txt"}]},
            )
        if request.url.path == "/api/v1/auth/logout":
            assert request.headers["origin"] == "http://127.0.0.1:5173"
            return httpx.Response(204)
        raise AssertionError(f"未预期的请求：{request.method} {request.url}")

    await run_smoke_test(
        "http://testserver",
        timeout_seconds=1,
        environ={"SMOKE_USERNAME": "admin", "SMOKE_PASSWORD": password},
        transport=httpx.MockTransport(handler),
    )

    assert paths == [
        "/health",
        "/ready",
        "/api/v1/auth/login",
        "/api/v1/auth/me",
        "/api/v1/knowledge-bases",
        "/api/v1/knowledge-bases/knowledge-base-id/documents",
        "/api/v1/documents/document-id",
        "/api/v1/knowledge-bases/knowledge-base-id/questions",
        "/api/v1/auth/logout",
    ]
    output = capsys.readouterr().out
    assert password not in output
    assert access_token not in output
    assert "sensitive-refresh-token" not in output


@pytest.mark.asyncio
async def test_run_smoke_test_reports_safe_structured_http_error() -> None:
    access_token = "sensitive-access-token"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in {"/health", "/ready"}:
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/api/v1/auth/login":
            return httpx.Response(200, json={"access_token": access_token})
        return httpx.Response(
            401,
            json={
                "error": {
                    "code": "ACCOUNT_DISABLED",
                    "message": "账号已停用。",
                    "request_id": "request-id-123",
                }
            },
        )

    with pytest.raises(SmokeTestError) as caught:
        await run_smoke_test(
            "http://testserver",
            timeout_seconds=1,
            environ={
                "SMOKE_USERNAME": "admin",
                "SMOKE_PASSWORD": "sensitive-password-123",
            },
            transport=httpx.MockTransport(handler),
        )

    message = str(caught.value)
    assert "HTTP 401" in message
    assert "ACCOUNT_DISABLED" in message
    assert "request-id-123" in message
    assert access_token not in message
    assert "sensitive-password-123" not in message

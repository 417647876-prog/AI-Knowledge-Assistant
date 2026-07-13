import json
from collections.abc import Awaitable, Callable
from typing import Annotated
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI, File, UploadFile

from app.api.middleware import UploadGuardMiddleware
from app.core.config import Settings
from app.core.security import create_access_token
from app.main import create_app

Receive = Callable[[], Awaitable[dict[str, object]]]


async def invoke_asgi(
    app,
    *,
    path: str,
    headers: list[tuple[bytes, bytes]],
    messages: list[dict[str, object]],
) -> tuple[list[dict[str, object]], int]:
    sent: list[dict[str, object]] = []
    receive_calls = 0

    async def receive() -> dict[str, object]:
        nonlocal receive_calls
        receive_calls += 1
        return messages.pop(0)

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
            "state": {"request_id": "upload-guard-001"},
        },
        receive,
        send,
    )
    return sent, receive_calls


def response_status(messages: list[dict[str, object]]) -> int:
    start = next(message for message in messages if message["type"] == "http.response.start")
    return int(start["status"])


def response_json(messages: list[dict[str, object]]) -> dict[str, object]:
    body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return json.loads(body)


def upload_path() -> str:
    return f"/api/v1/knowledge-bases/{uuid4()}/documents"


@pytest.mark.asyncio
async def test_fastapi_parses_multipart_before_route_dependency() -> None:
    body_read = False

    async def reject_after_parse() -> None:
        assert body_read is True
        raise RuntimeError("dependency ran after multipart parsing")

    app = FastAPI()

    @app.post("/upload")
    async def upload(
        file: Annotated[UploadFile, File()],
        _authorized: None = Depends(reject_after_parse),
    ) -> None:
        del file
        raise AssertionError("route must not run")

    boundary = "test-boundary"
    body = (
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
        'filename="a.txt"\r\n\r\ndata\r\n'
        f"--{boundary}--\r\n"
    ).encode()

    async def tracking_app(scope, receive, send) -> None:
        async def tracking_receive():
            nonlocal body_read
            message = await receive()
            body_read = True
            return message

        await app(scope, tracking_receive, send)

    with pytest.raises(RuntimeError, match="dependency ran after multipart parsing"):
        await invoke_asgi(
            tracking_app,
            path="/upload",
            headers=[
                (b"content-type", f"multipart/form-data; boundary={boundary}".encode()),
                (b"content-length", str(len(body)).encode()),
            ],
            messages=[{"type": "http.request", "body": body, "more_body": False}],
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("authorization", [None, b"Bearer invalid-token"])
async def test_upload_guard_rejects_unauthenticated_request_before_reading_body(
    authorization: bytes | None,
) -> None:
    settings = Settings(_env_file=None, max_upload_bytes=4, upload_multipart_overhead_bytes=8)

    async def downstream(_scope, _receive, _send) -> None:
        raise AssertionError("downstream must not run")

    headers = [(b"content-type", b"multipart/form-data; boundary=x")]
    if authorization is not None:
        headers.append((b"authorization", authorization))
    messages, receive_calls = await invoke_asgi(
        UploadGuardMiddleware(downstream, settings=settings),
        path=upload_path(),
        headers=headers,
        messages=[{"type": "http.request", "body": b"x" * 100, "more_body": False}],
    )

    assert response_status(messages) == 401
    assert receive_calls == 0
    expected_code = "AUTHENTICATION_REQUIRED" if authorization is None else "TOKEN_INVALID"
    assert response_json(messages) == {
        "error": {
            "code": expected_code,
            "message": (
                "用户未登录或账号已停用。" if authorization is None else "访问令牌无效或已过期。"
            ),
            "request_id": "upload-guard-001",
        }
    }


@pytest.mark.asyncio
async def test_upload_guard_rejects_authenticated_content_length_before_reading_body() -> None:
    settings = Settings(_env_file=None, max_upload_bytes=4, upload_multipart_overhead_bytes=8)
    token = create_access_token(user_id=uuid4(), role="user", settings=settings)

    async def downstream(_scope, _receive, _send) -> None:
        raise AssertionError("downstream must not run")

    messages, receive_calls = await invoke_asgi(
        UploadGuardMiddleware(downstream, settings=settings),
        path=upload_path(),
        headers=[
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-length", b"13"),
        ],
        messages=[{"type": "http.request", "body": b"x" * 13, "more_body": False}],
    )

    assert response_status(messages) == 413
    assert receive_calls == 0
    assert response_json(messages)["error"]["request_id"] == "upload-guard-001"


@pytest.mark.asyncio
async def test_upload_guard_rejects_authenticated_stream_as_soon_as_limit_is_exceeded() -> None:
    settings = Settings(_env_file=None, max_upload_bytes=4, upload_multipart_overhead_bytes=8)
    token = create_access_token(user_id=uuid4(), role="user", settings=settings)

    async def downstream(_scope, receive, send) -> None:
        try:
            while True:
                message = await receive()
                if not message.get("more_body", False):
                    break
        except Exception:
            # 模拟框架把解析异常转换成响应；守卫必须丢弃它，只发送一个 413。
            await send({"type": "http.response.start", "status": 400, "headers": []})
            await send({"type": "http.response.body", "body": b"framework response"})

    messages, receive_calls = await invoke_asgi(
        UploadGuardMiddleware(downstream, settings=settings),
        path=upload_path(),
        headers=[(b"authorization", f"Bearer {token}".encode())],
        messages=[
            {"type": "http.request", "body": b"x" * 8, "more_body": True},
            {"type": "http.request", "body": b"x" * 5, "more_body": True},
            {"type": "http.request", "body": b"never-read", "more_body": False},
        ],
    )

    assert response_status(messages) == 413
    assert sum(message["type"] == "http.response.start" for message in messages) == 1
    assert receive_calls == 2


@pytest.mark.asyncio
async def test_upload_guard_streams_allowed_body_without_request_buffering() -> None:
    settings = Settings(_env_file=None, max_upload_bytes=4, upload_multipart_overhead_bytes=8)
    token = create_access_token(user_id=uuid4(), role="user", settings=settings)
    received_bodies: list[bytes] = []

    async def downstream(_scope, receive, send) -> None:
        while True:
            message = await receive()
            received_bodies.append(message.get("body", b""))
            if not message.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 202, "headers": []})
        await send({"type": "http.response.body", "body": b"accepted"})

    messages, receive_calls = await invoke_asgi(
        UploadGuardMiddleware(downstream, settings=settings),
        path=upload_path(),
        headers=[(b"authorization", f"Bearer {token}".encode())],
        messages=[
            {"type": "http.request", "body": b"first", "more_body": True},
            {"type": "http.request", "body": b"second", "more_body": False},
        ],
    )

    assert response_status(messages) == 202
    assert received_bodies == [b"first", b"second"]
    assert receive_calls == 2


def test_real_app_rejects_anonymous_large_upload_with_request_id() -> None:
    from fastapi.testclient import TestClient

    response = TestClient(create_app()).post(
        upload_path(),
        content=b"x" * 1024,
        headers={
            "Content-Type": "multipart/form-data; boundary=x",
            "X-Request-ID": "anonymous-upload-001",
        },
    )

    assert response.status_code == 401
    assert response.headers["X-Request-ID"] == "anonymous-upload-001"
    assert response.json()["error"] == {
        "code": "AUTHENTICATION_REQUIRED",
        "message": "用户未登录或账号已停用。",
        "request_id": "anonymous-upload-001",
    }


@pytest.mark.asyncio
async def test_real_app_rejects_authenticated_stream_without_content_length_once() -> None:
    settings = Settings(_env_file=None, max_upload_bytes=4, upload_multipart_overhead_bytes=8)
    token = create_access_token(user_id=uuid4(), role="user", settings=settings)
    app = create_app()
    # create_app 的默认设置依赖是缓存对象；仅对本测试构建的中间件替换为小上限。
    upload_middleware = next(
        middleware for middleware in app.user_middleware if middleware.cls is UploadGuardMiddleware
    )
    upload_middleware.kwargs["settings"] = settings

    messages, receive_calls = await invoke_asgi(
        app,
        path=upload_path(),
        headers=[
            (b"authorization", f"Bearer {token}".encode()),
            (b"content-type", b"multipart/form-data; boundary=x"),
            (b"x-request-id", b"stream-upload-001"),
        ],
        messages=[
            {"type": "http.request", "body": b"--x\r\nCon", "more_body": True},
            {"type": "http.request", "body": b"tent-", "more_body": True},
            {"type": "http.request", "body": b"never-read", "more_body": False},
        ],
    )

    assert response_status(messages) == 413
    assert sum(message["type"] == "http.response.start" for message in messages) == 1
    assert response_json(messages)["error"]["request_id"] == "stream-upload-001"
    assert receive_calls == 2


@pytest.mark.asyncio
async def test_upload_guard_does_not_match_reprocess_route() -> None:
    called = False

    async def downstream(_scope, _receive, send) -> None:
        nonlocal called
        called = True
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    messages, receive_calls = await invoke_asgi(
        UploadGuardMiddleware(downstream, settings=Settings(_env_file=None)),
        path=f"/api/v1/documents/{uuid4()}/reprocess",
        headers=[(b"content-length", b"999999999")],
        messages=[{"type": "http.request", "body": b"", "more_body": False}],
    )

    assert called is True
    assert response_status(messages) == 204
    assert receive_calls == 0


@pytest.mark.asyncio
async def test_upload_guard_covers_all_upload_route_parameter_forms() -> None:
    async def downstream(_scope, _receive, _send) -> None:
        raise AssertionError("downstream must not run")

    messages, receive_calls = await invoke_asgi(
        UploadGuardMiddleware(downstream, settings=Settings(_env_file=None)),
        path="/api/v1/knowledge-bases/not-yet-validated/documents",
        headers=[],
        messages=[{"type": "http.request", "body": b"large-body", "more_body": False}],
    )

    assert response_status(messages) == 401
    assert receive_calls == 0

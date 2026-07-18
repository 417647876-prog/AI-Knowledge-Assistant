import httpx
import pytest

from scripts.verify_stage4_compose import (
    VerificationError,
    VerificationOptions,
    main,
    run_verification,
)


def _options() -> VerificationOptions:
    return VerificationOptions.from_inputs(
        base_url="http://testserver",
        username="stage4-user",
        password="stage4-password",
        request_timeout_seconds=1,
        wait_timeout_seconds=1,
        poll_interval_seconds=0.01,
    )


def test_options_require_credentials_without_echoing_secret() -> None:
    secret = "never-print-this-password"

    with pytest.raises(VerificationError) as caught:
        VerificationOptions.from_inputs(
            base_url="http://127.0.0.1:8080",
            username="",
            password=secret,
            request_timeout_seconds=5,
            wait_timeout_seconds=30,
            poll_interval_seconds=0.1,
        )

    assert "测试账号" in str(caught.value)
    assert secret not in str(caught.value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("request_timeout_seconds", 0),
        ("wait_timeout_seconds", -1),
        ("poll_interval_seconds", 0),
    ],
)
def test_options_reject_unbounded_or_invalid_timeouts(field: str, value: float) -> None:
    values = {
        "request_timeout_seconds": 5.0,
        "wait_timeout_seconds": 30.0,
        "poll_interval_seconds": 0.1,
    }
    values[field] = value

    with pytest.raises(VerificationError, match="超时和轮询间隔"):
        VerificationOptions.from_inputs(
            base_url="http://127.0.0.1:8080",
            username="stage4-user",
            password="stage4-password",
            **values,
        )


@pytest.mark.asyncio
async def test_health_failure_is_structured_and_never_echoes_response_body() -> None:
    secret_body = "password=server-leaked-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(
            503,
            headers={"x-request-id": "health-request-id"},
            text=secret_body,
        )

    with pytest.raises(VerificationError) as caught:
        await run_verification(_options(), transport=httpx.MockTransport(handler))

    message = str(caught.value)
    assert "健康检查" in message
    assert "HTTP 503" in message
    assert "health-request-id" in message
    assert secret_body not in message


@pytest.mark.asyncio
async def test_untrusted_request_id_is_sanitized() -> None:
    secret = _options().password
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            503,
            json={
                "error": {
                    "code": "NOT_READY",
                    "request_id": f"password={secret}",
                }
            },
        )
    )

    with pytest.raises(VerificationError) as caught:
        await run_verification(_options(), transport=transport)

    assert "request_id=unknown" in str(caught.value)
    assert secret not in str(caught.value)


@pytest.mark.asyncio
async def test_login_failure_returns_safe_non_success_result() -> None:
    secret = _options().password

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in {"/health", "/api/ready"}:
            return httpx.Response(200, headers={"x-request-id": "probe-id"})
        assert request.url.path == "/api/v1/auth/login"
        return httpx.Response(
            401,
            json={
                "error": {
                    "code": "INVALID_CREDENTIALS",
                    "message": f"bad {secret}",
                    "request_id": "login-request-id",
                }
            },
        )

    with pytest.raises(VerificationError) as caught:
        await run_verification(_options(), transport=httpx.MockTransport(handler))

    message = str(caught.value)
    assert "登录" in message
    assert "INVALID_CREDENTIALS" in message
    assert "login-request-id" in message
    assert secret not in message


@pytest.mark.asyncio
async def test_document_polling_has_a_hard_deadline() -> None:
    options = VerificationOptions.from_inputs(
        base_url="http://testserver",
        username="stage4-user",
        password="stage4-password",
        request_timeout_seconds=1,
        wait_timeout_seconds=0.02,
        poll_interval_seconds=0.001,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in {"/health", "/api/ready"}:
            return httpx.Response(200)
        if path == "/api/v1/auth/login":
            return httpx.Response(200, json={"access_token": "access-token"})
        if path == "/api/v1/knowledge-bases":
            return httpx.Response(201, json={"id": "knowledge-base-id"})
        if path.endswith("/documents"):
            return httpx.Response(202, json={"document_id": "document-id"})
        if path == "/api/v1/documents/document-id":
            return httpx.Response(200, json={"status": "pending"})
        raise AssertionError(f"未预期请求：{request.method} {path}")

    with pytest.raises(VerificationError, match="文档处理超时"):
        await run_verification(options, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_document_timeout_never_echoes_untrusted_status() -> None:
    secret_status = "password=server-leaked-secret"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in {"/health", "/api/ready"}:
            return httpx.Response(200)
        if path == "/api/v1/auth/login":
            return httpx.Response(200, json={"access_token": "access-token"})
        if path == "/api/v1/knowledge-bases":
            return httpx.Response(201, json={"id": "knowledge-base-id"})
        if path.endswith("/documents"):
            return httpx.Response(202, json={"document_id": "document-id"})
        if path == "/api/v1/documents/document-id":
            return httpx.Response(200, json={"status": secret_status})
        raise AssertionError(f"未预期请求：{request.method} {path}")

    with pytest.raises(VerificationError) as caught:
        await run_verification(_options(), transport=httpx.MockTransport(handler))

    assert "最后状态为 unknown" in str(caught.value)
    assert secret_status not in str(caught.value)


@pytest.mark.asyncio
async def test_sse_interruption_fails_without_echoing_answer_content() -> None:
    leaked_answer = "this-answer-must-not-be-printed"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path in {"/health", "/api/ready"}:
            return httpx.Response(200)
        if path == "/api/v1/auth/login":
            return httpx.Response(200, json={"access_token": "access-token"})
        if path == "/api/v1/knowledge-bases":
            return httpx.Response(201, json={"id": "knowledge-base-id"})
        if path.endswith("/documents"):
            return httpx.Response(202, json={"document_id": "document-id"})
        if path == "/api/v1/documents/document-id":
            return httpx.Response(200, json={"status": "ready"})
        if path.endswith("/conversations"):
            return httpx.Response(201, json={"id": "conversation-id"})
        if path.endswith("/messages/stream"):
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text=f'event: token\ndata: {{"delta":"{leaked_answer}"}}\n\n',
            )
        raise AssertionError(f"未预期请求：{request.method} {path}")

    with pytest.raises(VerificationError) as caught:
        await run_verification(_options(), transport=httpx.MockTransport(handler))

    message = str(caught.value)
    assert "SSE" in message
    assert "中断" in message
    assert leaked_answer not in message


@pytest.mark.asyncio
async def test_full_verification_closes_usage_feedback_and_restore_loops(
    capsys: pytest.CaptureFixture[str],
) -> None:
    paths: list[tuple[str, str]] = []
    leaked_values = {
        "stage4-password",
        "access-token",
        "refresh-token",
        "answer-body-must-stay-private",
    }
    trash_reads = 0

    def response(status_code: int = 200, **kwargs) -> httpx.Response:
        headers = {"x-request-id": f"request-{len(paths)}"}
        headers.update(kwargs.pop("headers", {}))
        return httpx.Response(
            status_code,
            headers=headers,
            **kwargs,
        )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal trash_reads
        path = request.url.path
        paths.append((request.method, path))
        if path in {"/health", "/api/ready"}:
            return response(json={"status": "ok"})
        if path == "/api/v1/auth/login":
            return response(
                headers={
                    "x-request-id": "login-id",
                    "set-cookie": "refresh_token=refresh-token; HttpOnly; Path=/api/v1/auth",
                },
                json={"access_token": "access-token"},
            )
        assert request.headers["authorization"] == "Bearer access-token"
        if path == "/api/v1/knowledge-bases" and request.method == "POST":
            return response(201, json={"id": "knowledge-base-id"})
        if path.endswith("/documents"):
            return response(202, json={"document_id": "document-id"})
        if path == "/api/v1/documents/document-id" and request.method == "GET":
            return response(json={"status": "ready"})
        if path.endswith("/conversations"):
            return response(201, json={"id": "conversation-id"})
        if path.endswith("/messages/stream"):
            return response(
                headers={
                    "x-request-id": "stream-http-id",
                    "content-type": "text/event-stream",
                },
                text=(
                    "event: token\n"
                    'data: {"delta":"answer-body-must-stay-private"}\n\n'
                    "event: done\n"
                    'data: {"request_id":"sse-request-id","citations":[]}\n\n'
                ),
            )
        if path == "/api/v1/conversations/conversation-id":
            return response(
                json={
                    "messages": [
                        {"id": "assistant-message-id", "role": "assistant", "status": "completed"}
                    ]
                }
            )
        if path == "/api/v1/me/usage":
            assert request.url.params.get("from")
            assert request.url.params.get("to")
            return response(json={"tokens": {"total_tokens": 3}, "purposes": {}})
        if path == "/api/v1/messages/assistant-message-id/feedback":
            return response(json={"id": "feedback-id"})
        if path == "/api/v1/me/feedback":
            return response(json={"items": [{"id": "feedback-id"}]})
        if path == "/api/v1/documents/document-id" and request.method == "DELETE":
            return response(204)
        if path == "/api/v1/trash":
            trash_reads += 1
            if trash_reads == 1:
                return response(json={"documents": [{"id": "document-id"}], "knowledge_bases": []})
            return response(
                json={"documents": [], "knowledge_bases": [{"id": "knowledge-base-id"}]}
            )
        if path == "/api/v1/documents/document-id/restore":
            return response(204)
        if path == "/api/v1/knowledge-bases/knowledge-base-id" and request.method == "DELETE":
            return response(204)
        if path == "/api/v1/knowledge-bases/knowledge-base-id/restore":
            return response(204)
        if path == "/api/v1/knowledge-bases" and request.method == "GET":
            return response(json=[{"id": "knowledge-base-id"}])
        raise AssertionError(f"未预期请求：{request.method} {path}")

    await run_verification(_options(), transport=httpx.MockTransport(handler))

    output = capsys.readouterr().out
    assert "[PASS] 阶段4验收" in output
    assert "sse-request-id" in output
    assert all(value not in output for value in leaked_values)
    assert ("POST", "/api/v1/documents/document-id/restore") in paths
    assert ("POST", "/api/v1/knowledge-bases/knowledge-base-id/restore") in paths


def test_main_returns_nonzero_when_credentials_are_missing(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        ["--base-url", "http://127.0.0.1:8080"],
        environ={"STAGE4_VERIFY_PASSWORD": "secret-must-not-leak"},
    )

    captured = capsys.readouterr()
    assert exit_code != 0
    assert "[FAIL]" in captured.err
    assert "secret-must-not-leak" not in captured.err

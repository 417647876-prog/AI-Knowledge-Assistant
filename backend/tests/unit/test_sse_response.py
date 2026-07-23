import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from starlette.requests import ClientDisconnect

from app.api import sse as sse_module
from app.api.sse import iter_sse
from app.api.v1 import questions as questions_module
from app.api.v1.questions import StreamQuestionRequest
from app.core.config import Settings
from app.core.exceptions import AppError
from app.rag.streaming import StreamEvent


class ConnectedRequest:
    async def is_disconnected(self) -> bool:
        return False


class DisconnectedRequest:
    async def is_disconnected(self) -> bool:
        return True


class ClosableSource:
    def __init__(self) -> None:
        self.closed = False
        self._never = asyncio.Event()

    def __aiter__(self):
        return self

    async def __anext__(self) -> StreamEvent:
        await self._never.wait()
        raise StopAsyncIteration

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_sse_adds_request_id_to_done() -> None:
    async def source():
        yield StreamEvent("done", {"citations": [], "timings": {}})

    body = b"".join([part async for part in iter_sse(ConnectedRequest(), source(), "req-1", 1)])

    assert b'"request_id":"req-1"' in body


@pytest.mark.asyncio
async def test_sse_sends_heartbeat_while_source_is_idle() -> None:
    async def source():
        await asyncio.sleep(0.02)
        yield StreamEvent("done", {})

    parts = [part async for part in iter_sse(ConnectedRequest(), source(), "req-2", 0.001)]

    assert b": ping\n\n" in parts


@pytest.mark.asyncio
async def test_sse_maps_app_error_without_leaking_exception() -> None:
    async def source():
        raise AppError(code="CHAT_PROVIDER_ERROR", message="模型不可用。", status_code=502)
        yield StreamEvent("token", {})

    body = b"".join([part async for part in iter_sse(ConnectedRequest(), source(), "req-3", 1)])

    assert b"event: error" in body
    assert b'"code":"CHAT_PROVIDER_ERROR"' in body
    assert b'"request_id":"req-3"' in body


@pytest.mark.asyncio
async def test_sse_maps_unexpected_error_without_leaking_exception() -> None:
    async def source():
        raise RuntimeError("vendor-secret")
        yield StreamEvent("token", {})

    body = b"".join([part async for part in iter_sse(ConnectedRequest(), source(), "req-4", 1)])

    assert b"event: error" in body
    assert b'"code":"CHAT_PROVIDER_ERROR"' in body
    assert b'"request_id":"req-4"' in body
    assert b"vendor-secret" not in body


@pytest.mark.asyncio
async def test_sse_closes_source_when_client_disconnects() -> None:
    source = ClosableSource()

    parts = [part async for part in iter_sse(DisconnectedRequest(), source, "req-5", 1)]

    assert parts == []
    assert source.closed


@pytest.mark.asyncio
async def test_sse_reports_disconnect_and_preserves_observed_partial_token() -> None:
    class DisconnectAfterFirstPoll:
        def __init__(self) -> None:
            self.polls = 0

        async def is_disconnected(self) -> bool:
            self.polls += 1
            return self.polls > 1

    observed: list[str] = []
    finalized: list[tuple[str, str | None]] = []

    async def source():
        yield StreamEvent("token", {"delta": "部分正文"})
        await asyncio.Event().wait()

    def on_event(event: StreamEvent) -> None:
        if event.event == "token":
            observed.append(str(event.data["delta"]))

    async def on_finalize(outcome: str, error_code: str | None) -> None:
        finalized.append((outcome, error_code))

    parts = [
        part
        async for part in iter_sse(
            DisconnectAfterFirstPoll(),
            source(),
            "req-disconnect",
            1,
            on_event=on_event,
            on_finalize=on_finalize,
        )
    ]

    assert "部分正文".encode() in b"".join(parts)
    assert observed == ["部分正文"]
    assert finalized == [("client_disconnected", "CLIENT_DISCONNECTED")]


@pytest.mark.asyncio
async def test_sse_distinguishes_cancellation_from_generator_close() -> None:
    async def idle_source():
        await asyncio.Event().wait()
        yield StreamEvent("done", {})

    canceled: list[tuple[str, str | None]] = []
    canceled_stream = iter_sse(
        ConnectedRequest(),
        idle_source(),
        "req-cancel",
        10,
        on_finalize=lambda outcome, code: _record(canceled, outcome, code),
    )
    pending = asyncio.create_task(anext(canceled_stream))
    await asyncio.sleep(0)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert canceled == [("canceled", "STREAM_CANCELED")]

    closed: list[tuple[str, str | None]] = []

    async def one_token_then_idle():
        yield StreamEvent("token", {"delta": "一"})
        await asyncio.Event().wait()

    closed_stream = iter_sse(
        ConnectedRequest(),
        one_token_then_idle(),
        "req-close",
        10,
        on_finalize=lambda outcome, code: _record(closed, outcome, code),
    )
    assert b"event: token" in await anext(closed_stream)
    await closed_stream.aclose()
    assert closed == [("client_disconnected", "CLIENT_DISCONNECTED")]


async def _record(
    target: list[tuple[str, str | None]],
    outcome: str,
    error_code: str | None,
) -> None:
    target.append((outcome, error_code))


@pytest.mark.asyncio
async def test_streaming_response_waits_for_close_when_asgi_send_raises_oserror() -> None:
    source_closed = asyncio.Event()
    finalized: list[tuple[str, str | None]] = []

    async def source():
        try:
            yield StreamEvent("token", {"delta": "部分正文"})
            await asyncio.Event().wait()
        finally:
            source_closed.set()

    response = sse_module.DisconnectAwareStreamingResponse(
        iter_sse(
            ConnectedRequest(),
            source(),
            "req-send-failed",
            10,
            on_finalize=lambda outcome, code: _record(finalized, outcome, code),
        )
    )

    async def receive():
        await asyncio.Event().wait()

    async def send(message) -> None:
        if message["type"] == "http.response.body":
            raise OSError("client disconnected")

    with pytest.raises(ClientDisconnect):
        await response(
            {"type": "http", "asgi": {"spec_version": "2.4"}},
            receive,
            send,
        )

    assert source_closed.is_set()
    assert finalized == [("client_disconnected", "CLIENT_DISCONNECTED")]
    assert not [
        task
        for task in asyncio.all_tasks()
        if not task.done() and "iter_sse.<locals>.produce" in repr(task.get_coro())
    ]


@pytest.mark.asyncio
async def test_legacy_questions_stream_waits_for_close_on_asgi_send_oserror(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_closed = asyncio.Event()

    async def allow_knowledge_base(*args, **kwargs):
        return object()

    monkeypatch.setattr(questions_module, "get_owned_knowledge_base", allow_knowledge_base)

    class LegacyService:
        async def stream_answer(self, *args, **kwargs):
            try:
                yield StreamEvent("token", {"delta": "旧接口部分正文"})
                await asyncio.Event().wait()
            finally:
                source_closed.set()

    class LegacyRequest:
        state = SimpleNamespace(request_id="legacy-send-failed")

        async def is_disconnected(self) -> bool:
            return False

    route = next(
        route
        for route in questions_module.router.routes
        if route.path == "/api/v1/knowledge-bases/{knowledge_base_id}/questions/stream"
    )
    assert route.deprecated is True

    response = await questions_module.stream_question(
        knowledge_base_id=uuid4(),
        payload=StreamQuestionRequest(question="旧接口问题"),
        request=LegacyRequest(),  # type: ignore[arg-type]
        session=object(),  # type: ignore[arg-type]
        current_user=object(),  # type: ignore[arg-type]
        service=LegacyService(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
    )
    assert response.media_type == "text/event-stream; charset=utf-8"
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"

    async def receive():
        await asyncio.Event().wait()

    async def send(message) -> None:
        if message["type"] == "http.response.body":
            raise OSError("ASGI 2.4 client disconnected")

    with pytest.raises(ClientDisconnect):
        await response(
            {"type": "http", "asgi": {"spec_version": "2.4"}},
            receive,
            send,
        )

    assert source_closed.is_set()
    assert not [
        task
        for task in asyncio.all_tasks()
        if not task.done() and "iter_sse.<locals>.produce" in repr(task.get_coro())
    ]

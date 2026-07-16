import asyncio

import pytest

from app.api.sse import iter_sse
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

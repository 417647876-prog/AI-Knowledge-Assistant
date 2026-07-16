import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Literal

from fastapi import Request
from starlette.responses import StreamingResponse
from starlette.types import Send

from app.core.exceptions import AppError
from app.rag.streaming import StreamEvent, encode_sse

_END = object()


async def _close_streaming_body(body_iterator) -> None:
    close = getattr(body_iterator, "aclose", None)
    if close is None:
        return
    close_task = asyncio.create_task(close())
    canceled = False
    while not close_task.done():
        try:
            await asyncio.shield(close_task)
        except asyncio.CancelledError:
            canceled = True
    await close_task
    if canceled:
        raise asyncio.CancelledError


class DisconnectAwareStreamingResponse(StreamingResponse):
    """等待 SSE 迭代器完成清理后再传播发送失败或取消。"""

    async def stream_response(self, send: Send) -> None:
        try:
            await super().stream_response(send)
        finally:
            await _close_streaming_body(self.body_iterator)


logger = logging.getLogger(__name__)


async def iter_sse(
    request: Request,
    source: AsyncIterator[StreamEvent],
    request_id: str,
    heartbeat_seconds: float = 15.0,
    on_event: Callable[[StreamEvent], None] | None = None,
    on_finalize: Callable[
        [Literal["completed", "client_disconnected", "canceled", "provider_failed"], str | None],
        Awaitable[None],
    ]
    | None = None,
) -> AsyncIterator[bytes]:
    """将问答事件编码为 SSE，并处理心跳、断连和安全错误事件。"""
    queue: asyncio.Queue[StreamEvent | AppError | object] = asyncio.Queue()
    finalized = False
    saw_done = False

    async def finalize(outcome, error_code: str | None) -> None:
        nonlocal finalized
        if finalized:
            return
        if on_finalize is not None:
            await on_finalize(outcome, error_code)
        finalized = True

    async def produce() -> None:
        nonlocal saw_done
        try:
            async for event in source:
                if on_event is not None:
                    on_event(event)
                if event.event == "done":
                    saw_done = True
                    await finalize("completed", None)
                if event.emit:
                    await queue.put(event)
        except AppError as error:
            await queue.put(error)
        except Exception:
            logger.exception("未处理的流式问答异常")
            await queue.put(
                AppError(
                    code="CHAT_PROVIDER_ERROR",
                    message="回答生成失败，请稍后重试。",
                    status_code=502,
                )
            )
        finally:
            await queue.put(_END)

    producer = asyncio.create_task(produce())
    outcome: Literal["completed", "client_disconnected", "canceled", "provider_failed"] = (
        "provider_failed"
    )
    error_code: str | None = "CHAT_PROVIDER_ERROR"
    try:
        while True:
            if await request.is_disconnected():
                outcome = "client_disconnected"
                error_code = "CLIENT_DISCONNECTED"
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except TimeoutError:
                yield b": ping\n\n"
                continue
            if item is _END:
                if saw_done:
                    outcome = "completed"
                    error_code = None
                break
            if isinstance(item, AppError):
                outcome = "provider_failed"
                error_code = item.code
                await finalize(outcome, error_code)
                yield encode_sse(
                    StreamEvent(
                        "error",
                        {
                            "code": item.code,
                            "message": item.message,
                            "request_id": request_id,
                        },
                    )
                )
                break
            data = dict(item.data)
            if item.event in {"done", "error"}:
                data["request_id"] = request_id
            yield encode_sse(StreamEvent(item.event, data))
    except asyncio.CancelledError:
        outcome = "canceled"
        error_code = "STREAM_CANCELED"
        raise
    except GeneratorExit:
        outcome = "client_disconnected"
        error_code = "CLIENT_DISCONNECTED"
        raise
    finally:
        producer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await producer
        close = getattr(source, "aclose", None)
        if close is not None:
            await close()
        await asyncio.shield(finalize(outcome, error_code))

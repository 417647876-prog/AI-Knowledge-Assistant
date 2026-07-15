import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator

from fastapi import Request

from app.core.exceptions import AppError
from app.rag.streaming import StreamEvent, encode_sse

_END = object()
logger = logging.getLogger(__name__)


async def iter_sse(
    request: Request,
    source: AsyncIterator[StreamEvent],
    request_id: str,
    heartbeat_seconds: float = 15.0,
) -> AsyncIterator[bytes]:
    """将问答事件编码为 SSE，并处理心跳、断连和安全错误事件。"""
    queue: asyncio.Queue[StreamEvent | AppError | object] = asyncio.Queue()

    async def produce() -> None:
        try:
            async for event in source:
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
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                item = await asyncio.wait_for(queue.get(), timeout=heartbeat_seconds)
            except TimeoutError:
                yield b": ping\n\n"
                continue
            if item is _END:
                break
            if isinstance(item, AppError):
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
    finally:
        producer.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await producer
        close = getattr(source, "aclose", None)
        if close is not None:
            await close()

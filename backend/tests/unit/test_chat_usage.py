import asyncio

import httpx
import pytest

from app.ai.chat import FakeChatProvider, OpenAICompatibleChatProvider
from app.ai.contracts import ChatCompletion, ChatStreamChunk, ChatUsage
from app.core.exceptions import AppError


def test_complete_chat_usage_rejects_inconsistent_token_totals() -> None:
    with pytest.raises(ValueError, match="total_tokens"):
        ChatUsage(
            cache_hit_input_tokens=10,
            cache_miss_input_tokens=5,
            output_tokens=3,
            reasoning_tokens=1,
            total_tokens=999,
            is_complete=True,
        )


def test_chat_stream_chunk_rejects_fields_from_other_chunk_kinds() -> None:
    with pytest.raises(ValueError, match="ChatUsage"):
        ChatStreamChunk(kind="usage", usage="not-usage")  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="字符串"):
        ChatStreamChunk(kind="done", finish_reason=42)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_fake_provider_returns_explicit_usage_for_generate_and_stream() -> None:
    usage = ChatUsage(4, 6, 2, 0, 12, True)
    provider = FakeChatProvider(
        answer="测试回答",
        tokens=["测试", "回答"],
        usage=usage,
        finish_reason="length",
        provider_request_id="fake-explicit-001",
    )

    completion = await provider.generate("system", "user")
    chunks = [chunk async for chunk in provider.stream("system", "user")]

    assert completion.usage is usage
    assert chunks[-2:] == [
        ChatStreamChunk(kind="usage", usage=usage),
        ChatStreamChunk(
            kind="done",
            finish_reason="length",
            provider_request_id="fake-explicit-001",
        ),
    ]


@pytest.mark.asyncio
async def test_generate_returns_deepseek_completion_with_real_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-deepseek-001",
                "choices": [
                    {
                        "message": {"content": "回答。[1]"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_cache_hit_tokens": 120,
                    "prompt_cache_miss_tokens": 30,
                    "completion_tokens": 20,
                    "completion_tokens_details": {"reasoning_tokens": 8},
                    "total_tokens": 170,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example/v1",
            api_key="private-key",
            model="chat-model",
        )

        completion = await provider.generate("system", "user")

    assert completion == ChatCompletion(
        content="回答。[1]",
        usage=ChatUsage(
            cache_hit_input_tokens=120,
            cache_miss_input_tokens=30,
            output_tokens=20,
            reasoning_tokens=8,
            total_tokens=170,
            is_complete=True,
        ),
        finish_reason="stop",
        provider_request_id="chatcmpl-deepseek-001",
    )


@pytest.mark.asyncio
async def test_generate_marks_partial_usage_incomplete_without_estimating_from_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-partial",
                "choices": [
                    {
                        "message": {"content": "这段正文很长，但不能拿字符数估算 Token。"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"completion_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )

        completion = await provider.generate("system", "user")

    assert completion.usage == ChatUsage(
        cache_hit_input_tokens=0,
        cache_miss_input_tokens=0,
        output_tokens=2,
        reasoning_tokens=0,
        total_tokens=0,
        is_complete=False,
    )
    assert completion.finish_reason == "length"


@pytest.mark.asyncio
async def test_generate_maps_openai_cached_tokens_to_hit_and_remaining_input_to_miss() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-openai-001",
                "choices": [{"message": {"content": "answer"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 100,
                    "prompt_tokens_details": {"cached_tokens": 40},
                    "completion_tokens": 15,
                    "completion_tokens_details": {"reasoning_tokens": 5},
                    "total_tokens": 115,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://api.openai.example/v1",
            api_key="private-key",
            model="chat-model",
        )

        completion = await provider.generate("system", "user")

    assert completion.usage == ChatUsage(40, 60, 15, 5, 115, True)


@pytest.mark.asyncio
async def test_generate_marks_excess_cached_tokens_incomplete_without_negative_miss() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "answer"}}],
                "usage": {
                    "prompt_tokens": 100,
                    "prompt_tokens_details": {"cached_tokens": 120},
                    "completion_tokens": 5,
                    "completion_tokens_details": {"reasoning_tokens": 0},
                    "total_tokens": 105,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )
        completion = await provider.generate("system", "user")

    assert completion.usage == ChatUsage(120, 0, 5, 0, 105, False)
    assert completion.usage.cache_miss_input_tokens >= 0


@pytest.mark.asyncio
async def test_generate_marks_provider_total_mismatch_incomplete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "answer"}}],
                "usage": {
                    "prompt_cache_hit_tokens": 10,
                    "prompt_cache_miss_tokens": 20,
                    "completion_tokens": 5,
                    "completion_tokens_details": {"reasoning_tokens": 1},
                    "total_tokens": 999,
                },
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )
        completion = await provider.generate("system", "user")

    assert completion.usage == ChatUsage(10, 20, 5, 1, 999, False)


@pytest.mark.asyncio
async def test_stream_emits_tokens_then_final_usage_and_done_for_deepseek_sse() -> None:
    body = (
        'data: {"id":"chatcmpl-stream-001","choices":[{"delta":{"content":"答"},'
        '"finish_reason":null}],"usage":null}\n\n'
        'data: {"id":"chatcmpl-stream-001","choices":[{"delta":{"content":"案"},'
        '"finish_reason":null}],"usage":null}\n\n'
        'data: {"id":"chatcmpl-stream-001","choices":[{"delta":{},'
        '"finish_reason":"stop"}],"usage":null}\n\n'
        'data: {"id":"chatcmpl-stream-001","choices":[],"usage":'
        '{"prompt_cache_hit_tokens":10,"prompt_cache_miss_tokens":5,'
        '"completion_tokens":4,"completion_tokens_details":{"reasoning_tokens":1},'
        '"total_tokens":19}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        request_payload = request.read()
        assert b'"stream":true' in request_payload
        assert b'"stream_options":{"include_usage":true}' in request_payload
        return httpx.Response(200, text=body)

    usage = ChatUsage(
        cache_hit_input_tokens=10,
        cache_miss_input_tokens=5,
        output_tokens=4,
        reasoning_tokens=1,
        total_tokens=19,
        is_complete=True,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )

        chunks = [chunk async for chunk in provider.stream("system", "user")]

    assert chunks == [
        ChatStreamChunk(kind="token", delta="答"),
        ChatStreamChunk(kind="token", delta="案"),
        ChatStreamChunk(kind="usage", usage=usage),
        ChatStreamChunk(
            kind="done",
            finish_reason="stop",
            provider_request_id="chatcmpl-stream-001",
        ),
    ]


@pytest.mark.asyncio
async def test_stream_assembles_multiline_data_frames_with_crlf_and_comments() -> None:
    body = (
        "event: message\r\n"
        "id: sse-id-is-not-provider-id\r\n"
        ": keep-alive\r\n"
        'data: {"id":"frame-001","choices":[\r\n'
        'data: {"delta":{"content":"跨行"},"finish_reason":"stop"}\r\n'
        'data: ],"usage":null}\r\n'
        "\r\n"
        "retry: 1000\r\n"
        ": usage follows\r\n"
        'data: {"id":"frame-001","choices":[],"usage":{\r\n'
        'data: "prompt_cache_hit_tokens":2,"prompt_cache_miss_tokens":3,\r\n'
        'data: "completion_tokens":1,\r\n'
        'data: "completion_tokens_details":{"reasoning_tokens":0},\r\n'
        'data: "total_tokens":6}}\r\n'
        "\r\n"
        "data: [DONE]\r\n"
        "\r\n"
        "data: not-json-after-done\r\n"
        "\r\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    usage = ChatUsage(2, 3, 1, 0, 6, True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )

        chunks = [chunk async for chunk in provider.stream("system", "user")]

    assert chunks == [
        ChatStreamChunk(kind="token", delta="跨行"),
        ChatStreamChunk(kind="usage", usage=usage),
        ChatStreamChunk(
            kind="done",
            finish_reason="stop",
            provider_request_id="frame-001",
        ),
    ]


@pytest.mark.asyncio
async def test_stream_dispatches_complete_final_frame_at_eof_and_allows_same_metadata() -> None:
    body = (
        'data: {"id":"eof-001","choices":[{"delta":{"content":"甲"},'
        '"finish_reason":"stop"}],"usage":null}\n\n'
        'data: {"id":"eof-001","choices":[{"delta":{"content":"乙"},'
        '"finish_reason":"stop"}],"usage":null}'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )
        chunks = [chunk async for chunk in provider.stream("system", "user")]

    assert chunks == [
        ChatStreamChunk(kind="token", delta="甲"),
        ChatStreamChunk(kind="token", delta="乙"),
    ]


@pytest.mark.asyncio
async def test_stream_locks_first_nonempty_request_id_and_finish_reason() -> None:
    body = (
        'data: {"id":"","choices":[{"delta":{"content":"甲"},'
        '"finish_reason":""}],"usage":null}\n\n'
        'data: {"id":"real-001","choices":[{"delta":{"content":"乙"},'
        '"finish_reason":"stop"}],"usage":null}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )
        chunks = [chunk async for chunk in provider.stream("system", "user")]

    assert chunks[-1] == ChatStreamChunk(
        kind="done",
        finish_reason="stop",
        provider_request_id="real-001",
    )


@pytest.mark.asyncio
async def test_stream_ending_before_usage_and_done_emits_only_received_tokens() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='data: {"id":"partial-001","choices":[{"delta":{"content":"半段"}}]}\n\n',
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )

        chunks = [chunk async for chunk in provider.stream("system", "user")]

    assert chunks == [ChatStreamChunk(kind="token", delta="半段")]


@pytest.mark.asyncio
async def test_stream_emits_identical_final_usage_only_once() -> None:
    usage_payload = (
        '{"prompt_cache_hit_tokens":2,"prompt_cache_miss_tokens":3,'
        '"completion_tokens":1,"completion_tokens_details":{"reasoning_tokens":0},'
        '"total_tokens":6}'
    )
    body = (
        f'data: {{"id":"duplicate-001","choices":[],"usage":{usage_payload}}}\n\n'
        f'data: {{"id":"duplicate-001","choices":[],"usage":{usage_payload}}}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )

        chunks = [chunk async for chunk in provider.stream("system", "user")]

    assert [chunk.kind for chunk in chunks] == ["usage", "done"]


@pytest.mark.asyncio
async def test_stream_rejects_same_usage_owned_by_different_provider_request_ids() -> None:
    usage_payload = (
        '{"prompt_cache_hit_tokens":2,"prompt_cache_miss_tokens":3,'
        '"completion_tokens":1,"completion_tokens_details":{"reasoning_tokens":0},'
        '"total_tokens":6}'
    )
    body = (
        f'data: {{"id":"owner-001","choices":[],"usage":{usage_payload}}}\n\n'
        f'data: {{"id":"owner-002","choices":[],"usage":{usage_payload}}}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )

        with pytest.raises(AppError) as captured:
            _ = [chunk async for chunk in provider.stream("system", "user")]

    assert captured.value.code == "CHAT_PROVIDER_ERROR"
    assert "owner-001" not in captured.value.message
    assert "owner-002" not in captured.value.message


@pytest.mark.asyncio
async def test_stream_rejects_conflicting_finish_reasons() -> None:
    body = (
        'data: {"id":"finish-001","choices":[{"delta":{},'
        '"finish_reason":"stop"}],"usage":null}\n\n'
        'data: {"id":"finish-001","choices":[{"delta":{},'
        '"finish_reason":"length"}],"usage":null}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )

        with pytest.raises(AppError) as captured:
            _ = [chunk async for chunk in provider.stream("system", "user")]

    assert captured.value.code == "CHAT_PROVIDER_ERROR"
    assert "stop" not in captured.value.message
    assert "length" not in captured.value.message


@pytest.mark.asyncio
async def test_stream_rejects_conflicting_duplicate_usage_with_stable_error() -> None:
    body = (
        'data: {"choices":[],"usage":{"prompt_cache_hit_tokens":2,'
        '"prompt_cache_miss_tokens":3,"completion_tokens":1,'
        '"completion_tokens_details":{"reasoning_tokens":0},"total_tokens":6}}\n\n'
        'data: {"choices":[],"usage":{"prompt_cache_hit_tokens":2,'
        '"prompt_cache_miss_tokens":3,"completion_tokens":2,'
        '"completion_tokens_details":{"reasoning_tokens":0},"total_tokens":7}}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )

        with pytest.raises(AppError) as captured:
            _ = [chunk async for chunk in provider.stream("system", "user")]

    assert captured.value.code == "CHAT_PROVIDER_ERROR"
    assert "private-key" not in captured.value.message


@pytest.mark.asyncio
async def test_stream_done_without_usage_preserves_finish_metadata_without_fake_usage() -> None:
    body = (
        "event: message\n"
        ": provider heartbeat\n"
        'data: {"id":"no-usage-001","choices":[{"delta":{},'
        '"finish_reason":"length"}],"usage":null}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )

        chunks = [chunk async for chunk in provider.stream("system", "user")]

    assert chunks == [
        ChatStreamChunk(
            kind="done",
            finish_reason="length",
            provider_request_id="no-usage-001",
        )
    ]


class _PausingByteStream(httpx.AsyncByteStream):
    def __init__(self) -> None:
        self.closed = False
        self._pause = asyncio.Event()

    async def __aiter__(self):
        yield ('data: {"id":"cancel-001","choices":[{"delta":{"content":"首段"}}]}\n\n').encode()
        await self._pause.wait()

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_stream_cancellation_propagates_and_closes_http_response() -> None:
    response_stream = _PausingByteStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=response_stream)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )
        chunks = provider.stream("system", "user")
        assert await anext(chunks) == ChatStreamChunk(kind="token", delta="首段")
        pending = asyncio.create_task(anext(chunks))
        await asyncio.sleep(0)
        pending.cancel()

        with pytest.raises(asyncio.CancelledError):
            await pending

    assert response_stream.closed is True

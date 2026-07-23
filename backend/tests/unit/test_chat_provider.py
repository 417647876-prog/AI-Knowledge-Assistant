import json

import httpx
import pytest

from app.ai.chat import FakeChatProvider, OpenAICompatibleChatProvider
from app.ai.contracts import ChatCompletion, ChatStreamChunk
from app.core.exceptions import AppError


@pytest.mark.asyncio
async def test_fake_chat_provider_returns_configured_answer() -> None:
    provider = FakeChatProvider(answer="固定答案。[1]")

    completion = await provider.generate("系统提示", "用户提示")

    assert completion == ChatCompletion(
        content="固定答案。[1]",
        usage=None,
        finish_reason="stop",
        provider_request_id="fake-chat-request",
    )


@pytest.mark.asyncio
async def test_openai_compatible_chat_provider_calls_chat_completions() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "回答。[1]"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example/v1",
            api_key="private-key",
            model="chat-model",
        )
        completion = await provider.generate("只依据上下文", "问题和上下文")

    assert completion.content == "回答。[1]"
    assert completion.usage is None
    assert str(captured[0].url) == "https://chat.example/v1/chat/completions"
    assert captured[0].headers["Authorization"] == "Bearer private-key"
    assert b'"stream":false' in captured[0].content


@pytest.mark.asyncio
async def test_chat_provider_sends_the_reserved_maximum_output_token_limit() -> None:
    captured: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "回答"}}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example/v1",
            api_key="private-key",
            model="chat-model",
        )
        await provider.generate("system", "user", max_output_tokens=321)
        _ = [item async for item in provider.stream("system", "user", max_output_tokens=654)]

    assert captured[0]["max_tokens"] == 321
    assert captured[1]["max_tokens"] == 654


@pytest.mark.asyncio
async def test_chat_provider_wraps_invalid_response_without_leaking_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "private-key"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )
        with pytest.raises(AppError) as error:
            await provider.generate("system", "user")

    assert error.value.code == "CHAT_PROVIDER_ERROR"
    assert "private-key" not in error.value.message


@pytest.mark.asyncio
async def test_fake_chat_provider_streams_configured_tokens() -> None:
    provider = FakeChatProvider(tokens=["答案", "。[1]"])

    assert [item async for item in provider.stream("system", "user")] == [
        ChatStreamChunk(kind="token", delta="答案"),
        ChatStreamChunk(kind="token", delta="。[1]"),
        ChatStreamChunk(
            kind="done",
            finish_reason="stop",
            provider_request_id="fake-chat-request",
        ),
    ]


@pytest.mark.asyncio
async def test_fake_chat_provider_streams_explicit_empty_tokens() -> None:
    provider = FakeChatProvider(answer="回退答案", tokens=[])

    assert [item async for item in provider.stream("system", "user")] == [
        ChatStreamChunk(
            kind="done",
            finish_reason="stop",
            provider_request_id="fake-chat-request",
        )
    ]


@pytest.mark.asyncio
async def test_openai_provider_reads_streaming_deltas_and_done() -> None:
    body = (
        'data: {"choices":[{"delta":{"content":"答案"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"。[1]"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert b'"stream":true' in request.content
        return httpx.Response(200, text=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example/v1",
            api_key="private-key",
            model="chat-model",
        )
        assert [item async for item in provider.stream("system", "user")] == [
            ChatStreamChunk(kind="token", delta="答案"),
            ChatStreamChunk(kind="token", delta="。[1]"),
            ChatStreamChunk(kind="done"),
        ]


@pytest.mark.asyncio
async def test_openai_provider_disables_deepseek_thinking_by_default() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["thinking"] == {"type": "disabled"}
        return httpx.Response(200, text="data: [DONE]\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )
        assert [item async for item in provider.stream("system", "user")] == [
            ChatStreamChunk(kind="done")
        ]


@pytest.mark.asyncio
async def test_openai_provider_skips_null_and_empty_streaming_deltas() -> None:
    body = (
        'data: {"choices":[{"delta":null}]}\n\n'
        'data: {"choices":[{"delta":{"content":null}}]}\n\n'
        'data: {"choices":[{"delta":{"content":""}}]}\n\n'
        'data: {"choices":[{"delta":{"content":"答案"}}]}\n\n'
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
        assert [item async for item in provider.stream("system", "user")] == [
            ChatStreamChunk(kind="token", delta="答案"),
            ChatStreamChunk(kind="done"),
        ]


@pytest.mark.asyncio
async def test_stream_wraps_invalid_provider_payload() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="data: not-json\n\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client, base_url="https://chat.example", api_key="secret", model="model"
        )
        with pytest.raises(AppError) as captured:
            _ = [item async for item in provider.stream("system", "user")]

    assert captured.value.code == "CHAT_PROVIDER_ERROR"
    assert "secret" not in captured.value.message


@pytest.mark.asyncio
async def test_stream_wraps_http_503_without_leaking_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream rejected private-key")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example",
            api_key="private-key",
            model="chat-model",
        )
        with pytest.raises(AppError) as captured:
            _ = [item async for item in provider.stream("system", "user")]

    assert captured.value.code == "CHAT_PROVIDER_ERROR"
    assert "private-key" not in captured.value.message


@pytest.mark.asyncio
async def test_stream_stops_reading_after_done() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(
                'data: {"choices":[{"delta":{"content":"答案"}}]}\n\n'
                "data: [DONE]\n\n"
                "data: not-json\n\n"
            ),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAICompatibleChatProvider(
            client=client, base_url="https://chat.example", api_key="secret", model="model"
        )

        assert [item async for item in provider.stream("system", "user")] == [
            ChatStreamChunk(kind="token", delta="答案"),
            ChatStreamChunk(kind="done"),
        ]

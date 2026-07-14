import httpx
import pytest

from app.ai.chat import FakeChatProvider, OpenAICompatibleChatProvider
from app.core.exceptions import AppError


@pytest.mark.asyncio
async def test_fake_chat_provider_returns_configured_answer() -> None:
    provider = FakeChatProvider(answer="固定答案。[1]")

    answer = await provider.generate("系统提示", "用户提示")

    assert answer == "固定答案。[1]"


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
        answer = await provider.generate("只依据上下文", "问题和上下文")

    assert answer == "回答。[1]"
    assert str(captured[0].url) == "https://chat.example/v1/chat/completions"
    assert captured[0].headers["Authorization"] == "Bearer private-key"
    assert b'"stream":false' in captured[0].content


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

    assert [item async for item in provider.stream("system", "user")] == ["答案", "。[1]"]


@pytest.mark.asyncio
async def test_fake_chat_provider_streams_explicit_empty_tokens() -> None:
    provider = FakeChatProvider(answer="回退答案", tokens=[])

    assert [item async for item in provider.stream("system", "user")] == []


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
        assert [item async for item in provider.stream("system", "user")] == ["答案", "。[1]"]


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
        assert [item async for item in provider.stream("system", "user")] == ["答案"]


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

        assert [item async for item in provider.stream("system", "user")] == ["答案"]

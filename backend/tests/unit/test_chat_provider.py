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

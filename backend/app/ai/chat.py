import json
from collections.abc import AsyncIterator

import httpx

from app.core.exceptions import AppError


def _chat_provider_error() -> AppError:
    return AppError(
        code="CHAT_PROVIDER_ERROR",
        message="问答模型服务暂时不可用。",
        status_code=502,
    )


class FakeChatProvider:
    def __init__(
        self,
        *,
        answer: str = "这是基于知识库的测试答案。[1]",
        tokens: list[str] | None = None,
    ) -> None:
        self._answer = answer
        self._tokens = tokens if tokens is not None else [answer]

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        return self._answer

    async def stream(self, system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
        for token in self._tokens:
            yield token


class OpenAICompatibleChatProvider:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient,
        base_url: str,
        api_key: str,
        model: str,
    ) -> None:
        self._client = client
        self._url = f"{base_url.rstrip('/')}/chat/completions"
        self._api_key = api_key
        self._model = model

    def _payload(
        self, system_prompt: str, user_prompt: str, *, stream: bool
    ) -> dict[str, object]:
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": stream,
        }

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=self._payload(system_prompt, user_prompt, stream=False),
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty chat response")
            return content.strip()
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise _chat_provider_error() from error

    async def stream(self, system_prompt: str, user_prompt: str) -> AsyncIterator[str]:
        try:
            async with self._client.stream(
                "POST",
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=self._payload(system_prompt, user_prompt, stream=True),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line.removeprefix("data:").strip()
                    if data == "[DONE]":
                        return
                    payload = json.loads(data)
                    delta = payload["choices"][0]["delta"].get("content")
                    if delta is not None:
                        if not isinstance(delta, str):
                            raise TypeError("invalid stream delta")
                        if delta:
                            yield delta
        except (
            httpx.HTTPError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
        ) as error:
            raise _chat_provider_error() from error

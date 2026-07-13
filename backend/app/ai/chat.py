import httpx

from app.core.exceptions import AppError


def _chat_provider_error() -> AppError:
    return AppError(
        code="CHAT_PROVIDER_ERROR",
        message="问答模型服务暂时不可用。",
        status_code=502,
    )


class FakeChatProvider:
    def __init__(self, *, answer: str = "这是基于知识库的测试答案。[1]") -> None:
        self._answer = answer

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        return self._answer


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

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "model": self._model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "stream": False,
                },
            )
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty chat response")
            return content.strip()
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise _chat_provider_error() from error

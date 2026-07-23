import json
from collections.abc import AsyncIterator

import httpx

from app.ai.contracts import ChatCompletion, ChatStreamChunk, ChatUsage
from app.core.exceptions import AppError


def _usage_token(mapping: dict[str, object], key: str) -> tuple[int, bool]:
    value = mapping.get(key)
    if value is None:
        return 0, False
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise TypeError(f"invalid usage field: {key}")
    return value, True


def _optional_string(mapping: dict[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"invalid string field: {key}")
    return value


def _lock_stream_value(current: str | None, incoming: str | None, field: str) -> str | None:
    if not incoming:
        return current
    if not current:
        return incoming
    if current != incoming:
        raise TypeError(f"conflicting stream field: {field}")
    return current


async def _iter_sse_data(lines: AsyncIterator[str]) -> AsyncIterator[str]:
    data_lines: list[str] = []
    async for line in lines:
        if line.endswith("\r"):
            line = line[:-1]
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue

        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "data":
            data_lines.append(value)

    if data_lines:
        yield "\n".join(data_lines)


def _parse_usage(raw_usage: object) -> ChatUsage | None:
    if raw_usage is None:
        return None
    if not isinstance(raw_usage, dict):
        raise TypeError("invalid usage")

    cache_hit, has_cache_hit = _usage_token(raw_usage, "prompt_cache_hit_tokens")
    cache_miss, has_cache_miss = _usage_token(raw_usage, "prompt_cache_miss_tokens")
    if not has_cache_hit and not has_cache_miss:
        prompt_tokens, has_prompt_tokens = _usage_token(raw_usage, "prompt_tokens")
        prompt_details = raw_usage.get("prompt_tokens_details")
        if prompt_details is None:
            prompt_details = {}
        if not isinstance(prompt_details, dict):
            raise TypeError("invalid prompt token details")
        cache_hit, has_cache_hit = _usage_token(prompt_details, "cached_tokens")
        if has_prompt_tokens and has_cache_hit and cache_hit <= prompt_tokens:
            cache_miss = prompt_tokens - cache_hit
            has_cache_miss = True

    output_tokens, has_output = _usage_token(raw_usage, "completion_tokens")
    completion_details = raw_usage.get("completion_tokens_details")
    if completion_details is None:
        completion_details = {}
    if not isinstance(completion_details, dict):
        raise TypeError("invalid completion token details")
    reasoning_tokens, has_reasoning = _usage_token(completion_details, "reasoning_tokens")
    total_tokens, has_total = _usage_token(raw_usage, "total_tokens")

    is_complete = (
        all((has_cache_hit, has_cache_miss, has_output, has_reasoning, has_total))
        and total_tokens == cache_hit + cache_miss + output_tokens
    )
    is_complete = is_complete and reasoning_tokens <= output_tokens
    return ChatUsage(
        cache_hit_input_tokens=cache_hit,
        cache_miss_input_tokens=cache_miss,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        total_tokens=total_tokens,
        is_complete=is_complete,
    )


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
        usage: ChatUsage | None = None,
        finish_reason: str | None = "stop",
        provider_request_id: str | None = "fake-chat-request",
    ) -> None:
        self._answer = answer
        self._tokens = tokens if tokens is not None else [answer]
        self._usage = usage
        self._finish_reason = finish_reason
        self._provider_request_id = provider_request_id

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_output_tokens: int | None = None,
    ) -> ChatCompletion:
        return ChatCompletion(
            content=self._answer,
            usage=self._usage,
            finish_reason=self._finish_reason,
            provider_request_id=self._provider_request_id,
        )

    async def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        for token in self._tokens:
            if token:
                yield ChatStreamChunk(kind="token", delta=token)
        if self._usage is not None:
            yield ChatStreamChunk(kind="usage", usage=self._usage)
        yield ChatStreamChunk(
            kind="done",
            finish_reason=self._finish_reason,
            provider_request_id=self._provider_request_id,
        )


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
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        stream: bool,
        max_output_tokens: int | None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": stream,
            "thinking": {"type": "disabled"},
        }
        if stream:
            payload["stream_options"] = {"include_usage": True}
        if max_output_tokens is not None:
            if (
                isinstance(max_output_tokens, bool)
                or not isinstance(max_output_tokens, int)
                or max_output_tokens <= 0
            ):
                raise ValueError("max_output_tokens 必须是正整数")
            payload["max_tokens"] = max_output_tokens
        return payload

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_output_tokens: int | None = None,
    ) -> ChatCompletion:
        try:
            response = await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=self._payload(
                    system_prompt,
                    user_prompt,
                    stream=False,
                    max_output_tokens=max_output_tokens,
                ),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise TypeError("invalid chat payload")
            choices = payload.get("choices")
            if not isinstance(choices, list) or not choices:
                raise TypeError("invalid chat choices")
            choice = choices[0]
            if not isinstance(choice, dict):
                raise TypeError("invalid chat choice")
            message = choice.get("message")
            if not isinstance(message, dict):
                raise TypeError("invalid chat message")
            content = message.get("content")
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty chat response")
            return ChatCompletion(
                content=content.strip(),
                usage=_parse_usage(payload.get("usage")),
                finish_reason=_optional_string(choice, "finish_reason"),
                provider_request_id=_optional_string(payload, "id"),
            )
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as error:
            raise _chat_provider_error() from error

    async def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[ChatStreamChunk]:
        finish_reason: str | None = None
        provider_request_id: str | None = None
        final_usage: ChatUsage | None = None
        try:
            async with self._client.stream(
                "POST",
                self._url,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=self._payload(
                    system_prompt,
                    user_prompt,
                    stream=True,
                    max_output_tokens=max_output_tokens,
                ),
            ) as response:
                response.raise_for_status()
                async for data in _iter_sse_data(response.aiter_lines()):
                    if data.strip() == "[DONE]":
                        yield ChatStreamChunk(
                            kind="done",
                            finish_reason=finish_reason,
                            provider_request_id=provider_request_id,
                        )
                        return
                    payload = json.loads(data)
                    if not isinstance(payload, dict):
                        raise TypeError("invalid stream payload")

                    request_id = _optional_string(payload, "id")
                    provider_request_id = _lock_stream_value(
                        provider_request_id,
                        request_id,
                        "provider_request_id",
                    )

                    choices = payload.get("choices")
                    if not isinstance(choices, list):
                        raise TypeError("invalid stream choices")
                    if choices:
                        choice = choices[0]
                        if not isinstance(choice, dict):
                            raise TypeError("invalid stream choice")
                        current_finish_reason = _optional_string(choice, "finish_reason")
                        finish_reason = _lock_stream_value(
                            finish_reason,
                            current_finish_reason,
                            "finish_reason",
                        )
                        delta_payload = choice.get("delta")
                        if delta_payload is not None:
                            if not isinstance(delta_payload, dict):
                                raise TypeError("invalid stream delta")
                            delta = delta_payload.get("content")
                            if delta is not None:
                                if not isinstance(delta, str):
                                    raise TypeError("invalid stream delta")
                                if delta:
                                    if final_usage is not None:
                                        raise TypeError("token received after final usage")
                                    yield ChatStreamChunk(kind="token", delta=delta)

                    usage = _parse_usage(payload.get("usage"))
                    if usage is not None:
                        if final_usage is None:
                            final_usage = usage
                            yield ChatStreamChunk(kind="usage", usage=usage)
                        elif usage != final_usage:
                            raise TypeError("conflicting final usage")
        except (
            httpx.HTTPError,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
            AttributeError,
            ValueError,
        ) as error:
            raise _chat_provider_error() from error

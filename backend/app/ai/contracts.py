from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class ConversationMessage:
    role: Literal["user", "assistant"]
    content: str

    def __post_init__(self) -> None:
        if self.role not in ("user", "assistant"):
            raise ValueError("role 必须是 'user' 或 'assistant'")


@dataclass(frozen=True)
class ChatUsage:
    cache_hit_input_tokens: int
    cache_miss_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    total_tokens: int
    is_complete: bool

    def __post_init__(self) -> None:
        token_fields = (
            "cache_hit_input_tokens",
            "cache_miss_input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "total_tokens",
        )
        for field_name in token_fields:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field_name} 必须是非负整数")
        if not isinstance(self.is_complete, bool):
            raise ValueError("is_complete 必须是布尔值")
        if self.is_complete:
            expected_total = (
                self.cache_hit_input_tokens + self.cache_miss_input_tokens + self.output_tokens
            )
            if self.total_tokens != expected_total:
                raise ValueError("total_tokens 与输入、输出 Token 之和不一致")
            if self.reasoning_tokens > self.output_tokens:
                raise ValueError("reasoning_tokens 不能大于 output_tokens")


@dataclass(frozen=True)
class ChatCompletion:
    content: str
    usage: ChatUsage | None
    finish_reason: str | None
    provider_request_id: str | None


@dataclass(frozen=True)
class ChatStreamChunk:
    kind: Literal["token", "usage", "done"]
    delta: str | None = None
    usage: ChatUsage | None = None
    finish_reason: str | None = None
    provider_request_id: str | None = None

    def __post_init__(self) -> None:
        if self.kind == "token":
            if not isinstance(self.delta, str) or not self.delta:
                raise ValueError("token 块必须携带非空 delta")
            if any(
                value is not None
                for value in (self.usage, self.finish_reason, self.provider_request_id)
            ):
                raise ValueError("token 块只能携带 delta")
            return
        if self.kind == "usage":
            if not isinstance(self.usage, ChatUsage):
                raise ValueError("usage 块必须携带 ChatUsage")
            if any(
                value is not None
                for value in (self.delta, self.finish_reason, self.provider_request_id)
            ):
                raise ValueError("usage 块只能携带 usage")
            return
        if self.kind == "done":
            if self.delta is not None or self.usage is not None:
                raise ValueError("done 块不能携带 delta 或 usage")
            if any(
                value is not None and not isinstance(value, str)
                for value in (self.finish_reason, self.provider_request_id)
            ):
                raise ValueError("done 块的结束原因和请求编号必须是字符串或 None")
            return
        raise ValueError("kind 必须是 'token'、'usage' 或 'done'")


class EmbeddingProvider(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class RerankerProvider(Protocol):
    async def rerank(self, query: str, documents: list[str]) -> list[float]: ...


class ChatProvider(Protocol):
    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_output_tokens: int | None = None,
    ) -> ChatCompletion: ...


class StreamingChatProvider(ChatProvider, Protocol):
    def stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        max_output_tokens: int | None = None,
    ) -> AsyncIterator[ChatStreamChunk]: ...


class QuestionRewriter(Protocol):
    async def rewrite(self, history: list[ConversationMessage], question: str) -> str: ...

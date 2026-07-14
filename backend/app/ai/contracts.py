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


class EmbeddingProvider(Protocol):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_query(self, text: str) -> list[float]: ...


class ChatProvider(Protocol):
    async def generate(self, system_prompt: str, user_prompt: str) -> str: ...


class StreamingChatProvider(ChatProvider, Protocol):
    def stream(self, system_prompt: str, user_prompt: str) -> AsyncIterator[str]: ...


class QuestionRewriter(Protocol):
    async def rewrite(
        self, history: list[ConversationMessage], question: str
    ) -> str: ...

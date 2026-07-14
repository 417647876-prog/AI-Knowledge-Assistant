from uuid import uuid4

import pytest

from app.ai.embeddings import FakeEmbeddingProvider
from app.core.exceptions import AppError
from app.rag.schemas import RetrievedChunk
from app.rag.service import RagService


class FakeSession:
    def __init__(self, knowledge_base: object | None) -> None:
        self.knowledge_base = knowledge_base

    async def get(self, model, identifier):
        return self.knowledge_base


class StubRetriever:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, object]] = []

    async def search(self, **kwargs) -> list[RetrievedChunk]:
        self.calls.append(kwargs)
        return self.chunks


class CountingChatProvider:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.call_count = 0

    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        self.call_count += 1
        return self.answer


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_name="员工手册.pdf",
        content="入职满一年可享受五天年假。",
        relevance_score=0.92,
        page_number=12,
    )


@pytest.mark.asyncio
async def test_answer_retrieves_generates_and_maps_real_citations() -> None:
    chunk = _chunk()
    retriever = StubRetriever([chunk])
    chat = CountingChatProvider("员工可享受五天年假。[1][99]")
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=retriever,
        chat_provider=chat,
        score_threshold=0.55,
    )
    knowledge_base_id = uuid4()

    result = await service.answer(
        knowledge_base_id=knowledge_base_id,
        question="年假有几天？",
        top_k=5,
    )

    assert result.answer == "员工可享受五天年假。[1][99]"
    assert result.retrieved_chunk_count == 1
    assert [item.citation_id for item in result.citations] == [1]
    assert retriever.calls[0]["knowledge_base_id"] == knowledge_base_id
    assert retriever.calls[0]["score_threshold"] == 0.55
    assert chat.call_count == 1


@pytest.mark.asyncio
async def test_answer_refuses_without_chunks_and_does_not_call_chat() -> None:
    chat = CountingChatProvider("不应该被调用")
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever([]),
        chat_provider=chat,
        score_threshold=0.55,
    )

    result = await service.answer(uuid4(), "不存在的制度", 5)

    assert result.answer == "未找到足够依据，无法根据当前知识库回答该问题。"
    assert result.citations == []
    assert result.retrieved_chunk_count == 0
    assert chat.call_count == 0


@pytest.mark.asyncio
async def test_answer_rejects_missing_knowledge_base() -> None:
    retriever = StubRetriever([])
    service = RagService(
        session=FakeSession(None),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=retriever,
        chat_provider=CountingChatProvider("unused"),
        score_threshold=0.55,
    )

    with pytest.raises(AppError) as error:
        await service.answer(uuid4(), "问题", 5)

    assert error.value.code == "KNOWLEDGE_BASE_NOT_FOUND"
    assert retriever.calls == []

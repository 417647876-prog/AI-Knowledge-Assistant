import asyncio
from uuid import uuid4

import pytest

from app.rag.hybrid_retriever import HybridRetriever
from app.rag.schemas import RetrievedChunk


class StubRetriever:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, object]] = []

    async def search(self, **kwargs) -> list[RetrievedChunk]:
        self.calls.append(kwargs)
        return self.chunks


class SharedSessionGuard:
    def __init__(self) -> None:
        self.active = False


class GuardedRetriever(StubRetriever):
    def __init__(self, chunks: list[RetrievedChunk], guard: SharedSessionGuard) -> None:
        super().__init__(chunks)
        self.guard = guard

    async def search(self, **kwargs) -> list[RetrievedChunk]:
        if self.guard.active:
            raise RuntimeError("共享数据库会话发生并发调用")
        self.guard.active = True
        try:
            await asyncio.sleep(0)
            return await super().search(**kwargs)
        finally:
            self.guard.active = False


def _chunk(name: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_name=f"{name}.txt",
        content=name,
        relevance_score=0.8,
    )


@pytest.mark.asyncio
async def test_hybrid_retriever_requests_both_paths_and_fuses_results() -> None:
    vector_only = _chunk("向量")
    shared = _chunk("共同")
    keyword_only = _chunk("关键词")
    vector = StubRetriever([vector_only, shared])
    keyword = StubRetriever([shared, keyword_only])
    retriever = HybridRetriever(vector, keyword, rank_constant=60)
    knowledge_base_id = uuid4()

    results = await retriever.search(
        knowledge_base_id=knowledge_base_id,
        query="VPN2026 账号",
        query_embedding=[0.1, 0.2],
        top_k=2,
        score_threshold=0.55,
    )

    expected_call = {
        "knowledge_base_id": knowledge_base_id,
        "query": "VPN2026 账号",
        "query_embedding": [0.1, 0.2],
        "top_k": 2,
        "score_threshold": 0.55,
    }
    assert vector.calls == [expected_call]
    assert keyword.calls == [expected_call]
    assert [item.chunk_id for item in results] == [shared.chunk_id, vector_only.chunk_id]


@pytest.mark.asyncio
async def test_hybrid_retriever_does_not_overlap_shared_session_calls() -> None:
    guard = SharedSessionGuard()
    vector = GuardedRetriever([_chunk("向量")], guard)
    keyword = GuardedRetriever([_chunk("关键词")], guard)

    results = await HybridRetriever(vector, keyword).search(
        knowledge_base_id=uuid4(),
        query="问题",
        query_embedding=[0.1],
        top_k=2,
        score_threshold=0.5,
    )

    assert len(results) == 2

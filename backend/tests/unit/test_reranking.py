from uuid import UUID

import pytest

from app.core.exceptions import AppError
from app.rag.reranking import rerank_chunks
from app.rag.schemas import RetrievedChunk


class StubReranker:
    def __init__(self, scores: list[object]) -> None:
        self.scores = scores
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, documents))
        return self.scores  # type: ignore[return-value]


def make_chunk(number: int, *, score: float = 0.1) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=UUID(int=number),
        document_id=UUID(int=number + 100),
        file_name=f"制度-{number}.md",
        content=f"候选片段 {number}",
        relevance_score=score,
    )


@pytest.mark.asyncio
async def test_rerank_chunks_replaces_score_and_orders_descending() -> None:
    first = make_chunk(1)
    second = make_chunk(2)
    provider = StubReranker([0.2, 0.9])

    result = await rerank_chunks(
        provider,
        query="年假",
        chunks=[first, second],
        top_k=2,
    )

    assert provider.calls == [("年假", [first.content, second.content])]
    assert [item.chunk_id for item in result] == [second.chunk_id, first.chunk_id]
    assert [item.relevance_score for item in result] == [0.9, 0.2]
    assert first.relevance_score == 0.1


@pytest.mark.asyncio
async def test_rerank_chunks_preserves_original_order_for_equal_scores() -> None:
    chunks = [make_chunk(1), make_chunk(2), make_chunk(3)]

    result = await rerank_chunks(
        StubReranker([0.7, 0.9, 0.9]),
        query="报销",
        chunks=chunks,
        top_k=2,
    )

    assert [item.chunk_id for item in result] == [chunks[1].chunk_id, chunks[2].chunk_id]


@pytest.mark.asyncio
async def test_rerank_chunks_rejects_score_count_mismatch() -> None:
    with pytest.raises(AppError) as exc_info:
        await rerank_chunks(
            StubReranker([0.8]),
            query="密码",
            chunks=[make_chunk(1), make_chunk(2)],
            top_k=2,
        )

    assert exc_info.value.code == "RERANKER_PROVIDER_ERROR"
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_score", [float("nan"), float("inf"), float("-inf"), "secret-score"]
)
async def test_rerank_chunks_rejects_invalid_scores_without_leaking_values(
    invalid_score: object,
) -> None:
    secret_query = "secret-query"

    with pytest.raises(AppError) as exc_info:
        await rerank_chunks(
            StubReranker([invalid_score]),
            query=secret_query,
            chunks=[make_chunk(1)],
            top_k=1,
        )

    assert exc_info.value.code == "RERANKER_PROVIDER_ERROR"
    assert exc_info.value.status_code == 502
    assert str(invalid_score) not in exc_info.value.message
    assert secret_query not in exc_info.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize("top_k", [0, -1, 3])
async def test_rerank_chunks_rejects_invalid_top_k(top_k: int) -> None:
    provider = StubReranker([0.9, 0.8])

    with pytest.raises(ValueError, match="top_k"):
        await rerank_chunks(
            provider,
            query="制度",
            chunks=[make_chunk(1), make_chunk(2)],
            top_k=top_k,
        )

    assert provider.calls == []

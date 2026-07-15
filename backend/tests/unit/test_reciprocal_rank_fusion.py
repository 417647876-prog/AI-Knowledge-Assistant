from uuid import UUID

import pytest

from app.rag.fusion import rrf_fuse
from app.rag.schemas import RetrievedChunk


def _chunk(identifier: int) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=UUID(int=identifier),
        document_id=UUID(int=identifier + 100),
        file_name=f"{identifier}.txt",
        content=f"片段 {identifier}",
        relevance_score=0.9,
    )


def test_rrf_rewards_chunk_found_by_both_retrievers() -> None:
    vector_only = _chunk(1)
    shared = _chunk(2)
    keyword_only = _chunk(3)

    fused = rrf_fuse([[vector_only, shared], [shared, keyword_only]], top_k=3)

    assert [item.chunk_id for item in fused] == [
        shared.chunk_id,
        vector_only.chunk_id,
        keyword_only.chunk_id,
    ]
    assert fused[0].relevance_score == pytest.approx(1 / 62 + 1 / 61)


def test_rrf_counts_duplicate_chunk_once_per_ranked_list() -> None:
    lower_id = _chunk(1)
    duplicate = _chunk(2)

    fused = rrf_fuse([[duplicate, duplicate], [lower_id]], top_k=2)

    assert [item.chunk_id for item in fused] == [lower_id.chunk_id, duplicate.chunk_id]
    assert fused[0].relevance_score == pytest.approx(fused[1].relevance_score)


def test_rrf_uses_stable_chunk_id_order_and_applies_top_k() -> None:
    higher_id = _chunk(20)
    lower_id = _chunk(10)

    fused = rrf_fuse([[higher_id], [lower_id]], top_k=1)

    assert [item.chunk_id for item in fused] == [lower_id.chunk_id]

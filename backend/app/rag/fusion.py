from collections import defaultdict
from dataclasses import replace
from uuid import UUID

from app.rag.schemas import RetrievedChunk


def rrf_fuse(
    ranked_lists: list[list[RetrievedChunk]],
    *,
    top_k: int,
    rank_constant: int = 60,
) -> list[RetrievedChunk]:
    chunks: dict[UUID, RetrievedChunk] = {}
    scores: dict[UUID, float] = defaultdict(float)

    for ranked_list in ranked_lists:
        seen: set[UUID] = set()
        for rank, chunk in enumerate(ranked_list, start=1):
            if chunk.chunk_id in seen:
                continue
            seen.add(chunk.chunk_id)
            chunks.setdefault(chunk.chunk_id, chunk)
            scores[chunk.chunk_id] += 1 / (rank_constant + rank)

    ordered_ids = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], str(chunk_id)))
    return [
        replace(chunks[chunk_id], relevance_score=scores[chunk_id])
        for chunk_id in ordered_ids[:top_k]
    ]

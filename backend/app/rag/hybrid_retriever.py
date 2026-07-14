import asyncio
from uuid import UUID

from app.rag.contracts import Retriever
from app.rag.fusion import rrf_fuse
from app.rag.schemas import RetrievedChunk


class HybridRetriever:
    def __init__(
        self,
        vector: Retriever,
        keyword: Retriever,
        *,
        rank_constant: int = 60,
    ) -> None:
        self._vector = vector
        self._keyword = keyword
        self._rank_constant = rank_constant

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        query: str,
        query_embedding: list[float],
        top_k: int,
        score_threshold: float,
    ) -> list[RetrievedChunk]:
        search_arguments = {
            "knowledge_base_id": knowledge_base_id,
            "query": query,
            "query_embedding": query_embedding,
            "top_k": top_k,
            "score_threshold": score_threshold,
        }
        vector_chunks, keyword_chunks = await asyncio.gather(
            self._vector.search(**search_arguments),
            self._keyword.search(**search_arguments),
        )
        return rrf_fuse(
            [vector_chunks, keyword_chunks],
            top_k=top_k,
            rank_constant=self._rank_constant,
        )

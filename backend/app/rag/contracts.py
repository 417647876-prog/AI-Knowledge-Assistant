from typing import Protocol
from uuid import UUID

from app.rag.schemas import RetrievedChunk


class Retriever(Protocol):
    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        query: str,
        query_embedding: list[float],
        top_k: int,
        score_threshold: float,
    ) -> list[RetrievedChunk]: ...

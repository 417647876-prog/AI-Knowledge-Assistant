import math
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.knowledge.search_tokens import build_search_text
from app.rag.schemas import RetrievedChunk

_MIN_QUERY_TOKEN_COVERAGE = 0.2
_TOKEN_RANK_WEIGHT = 0.1


class KeywordRetriever:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        query: str,
        query_embedding: list[float],
        top_k: int,
        score_threshold: float,
    ) -> list[RetrievedChunk]:
        tokens = build_search_text(query).split()
        if not tokens:
            return []

        minimum_matches = math.ceil(len(tokens) * _MIN_QUERY_TOKEN_COVERAGE)
        ts_query = func.to_tsquery("simple", " | ".join(tokens))
        rank = func.ts_rank_cd(DocumentChunk.search_vector, ts_query).label("relevance_score")
        statement = (
            select(DocumentChunk, Document.original_file_name, rank)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(DocumentChunk.knowledge_base_id == knowledge_base_id)
            .where(DocumentChunk.search_vector.bool_op("@@")(ts_query))
            .where(rank >= minimum_matches * _TOKEN_RANK_WEIGHT)
            .order_by(rank.desc(), DocumentChunk.id.asc())
            .limit(top_k)
        )
        rows = (await self._session.execute(statement)).all()
        return [
            RetrievedChunk(
                chunk_id=chunk.id,
                document_id=chunk.document_id,
                file_name=file_name,
                content=chunk.content,
                relevance_score=float(relevance_score),
                page_number=chunk.page_number,
                sheet_name=chunk.sheet_name,
                row_start=chunk.row_start,
                section_title=chunk.section_title,
            )
            for chunk, file_name, relevance_score in rows
        ]

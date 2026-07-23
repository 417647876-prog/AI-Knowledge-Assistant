from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.rag.schemas import RetrievedChunk


class VectorRetriever:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        query: str = "",
        query_embedding: list[float],
        top_k: int,
        score_threshold: float,
    ) -> list[RetrievedChunk]:
        distance = DocumentChunk.embedding.cosine_distance(query_embedding)
        score = (1 - distance).label("relevance_score")
        statement = (
            select(DocumentChunk, Document.original_file_name, score)
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                DocumentChunk.knowledge_base_id == knowledge_base_id,
                Document.deleted_at.is_(None),
            )
            .where(score >= score_threshold)
            .order_by(distance)
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

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import case, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.contracts import EmbeddingProvider
from app.core.exceptions import AppError
from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.ingestion_job import IngestionJob
from app.knowledge.chunking import RecursiveTextChunker
from app.knowledge.parsers.registry import ParserRegistry
from app.knowledge.search_tokens import build_search_text


class IngestionService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        upload_directory: Path,
        parser_registry: ParserRegistry,
        chunker: RecursiveTextChunker,
        embedding_provider: EmbeddingProvider,
        embedding_dimensions: int,
    ) -> None:
        self._session = session
        self._upload_directory = upload_directory
        self._parser_registry = parser_registry
        self._chunker = chunker
        self._embedding_provider = embedding_provider
        self._embedding_dimensions = embedding_dimensions

    async def process(self, document_id: UUID) -> None:
        document = await self._session.get(Document, document_id)
        if document is None:
            raise AppError(code="DOCUMENT_NOT_FOUND", message="文档不存在。", status_code=404)
        job = await self._latest_job(document_id)
        if job is None:
            raise AppError(code="DOCUMENT_NOT_FOUND", message="文档任务不存在。", status_code=404)

        try:
            await self._update_stage(document, job, document_status="parsing", job_stage="parse")
            parser = self._parser_registry.get_parser(document.file_extension)
            sections = parser.parse(self._upload_directory / document.stored_file_name)
            chunks = self._chunker.split(sections)
            if not chunks:
                raise AppError(
                    code="DOCUMENT_CONTENT_EMPTY", message="文档没有可入库的内容。", status_code=422
                )

            await self._update_stage(document, job, document_status="embedding", job_stage="embed")
            embeddings = await self._embedding_provider.embed_documents(
                [chunk.content for chunk in chunks]
            )
            self._validate_embeddings(chunks_count=len(chunks), embeddings=embeddings)

            await self._session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                self._session.add(
                    DocumentChunk(
                        document_id=document.id,
                        knowledge_base_id=document.knowledge_base_id,
                        chunk_index=chunk.chunk_index,
                        content=chunk.content,
                        content_hash=chunk.content_hash,
                        page_number=chunk.page_number,
                        sheet_name=chunk.sheet_name,
                        row_start=chunk.row_start,
                        section_title=chunk.section_title,
                        start_index=chunk.start_index,
                        extra_metadata=chunk.metadata,
                        embedding=embedding,
                        search_text=build_search_text(chunk.content),
                    )
                )
            document.status = "ready"
            document.error_code = None
            document.error_message = None
            job.status = "succeeded"
            job.stage = "store"
            job.chunk_count = len(chunks)
            job.finished_at = datetime.now(UTC)
            job.error_code = None
            job.error_message = None
            await self._session.commit()
        except Exception as error:
            await self._mark_failed(document_id, error)
            raise

    async def _latest_job(self, document_id: UUID) -> IngestionJob | None:
        return await self._session.scalar(
            select(IngestionJob)
            .where(IngestionJob.document_id == document_id)
            .order_by(
                case(
                    (IngestionJob.status == "pending", 0),
                    (IngestionJob.status == "running", 1),
                    else_=2,
                ),
                IngestionJob.created_at.desc(),
                IngestionJob.id.desc(),
            )
        )

    async def _update_stage(
        self,
        document: Document,
        job: IngestionJob,
        *,
        document_status: str,
        job_stage: str,
    ) -> None:
        document.status = document_status
        job.status = "running"
        job.stage = job_stage
        if job.started_at is None:
            job.started_at = datetime.now(UTC)
        await self._session.commit()

    def _validate_embeddings(self, *, chunks_count: int, embeddings: list[list[float]]) -> None:
        if len(embeddings) != chunks_count or any(
            len(embedding) != self._embedding_dimensions for embedding in embeddings
        ):
            raise AppError(
                code="EMBEDDING_PROVIDER_ERROR",
                message="Embedding 返回结果不符合入库要求。",
                status_code=502,
            )

    async def _mark_failed(self, document_id: UUID, error: Exception) -> None:
        await self._session.rollback()
        document = await self._session.get(Document, document_id)
        job = await self._latest_job(document_id)
        if document is None or job is None:
            return
        if isinstance(error, AppError):
            code, message = error.code, error.message
        else:
            code, message = "DOCUMENT_PROCESSING_ERROR", "文档处理失败。"
        document.status = "failed"
        document.error_code = code
        document.error_message = message
        job.status = "failed"
        job.error_code = code
        job.error_message = message
        job.finished_at = datetime.now(UTC)
        await self._session.commit()

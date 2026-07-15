from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.contracts import EmbeddingProvider
from app.core.exceptions import AppError
from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.document_job import DocumentJob
from app.db.models.knowledge_base import KnowledgeBase
from app.jobs.repository import LeaseLostError, update_job_stage
from app.knowledge.chunking import RecursiveTextChunker, TextChunk
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

    async def process(
        self,
        *,
        document_id: UUID,
        job_id: UUID,
        lease_token: UUID,
    ) -> int:
        document = await self._checkpoint(
            document_id=document_id,
            job_id=job_id,
            lease_token=lease_token,
            stage="parse",
        )
        file_path = self._stored_file_path(document.stored_file_name)
        parser = self._parser_registry.get_parser(document.file_extension)
        sections = parser.parse(file_path)

        await self._checkpoint(
            document_id=document_id,
            job_id=job_id,
            lease_token=lease_token,
            stage="embed",
        )
        chunks = self._chunker.split(sections)
        if not chunks:
            raise AppError(
                code="DOCUMENT_CONTENT_EMPTY",
                message="文档没有可入库的内容。",
                status_code=422,
            )

        embeddings = await self._embedding_provider.embed_documents(
            [chunk.content for chunk in chunks]
        )
        self._validate_embeddings(chunks_count=len(chunks), embeddings=embeddings)

        await self._checkpoint(
            document_id=document_id,
            job_id=job_id,
            lease_token=lease_token,
        )
        return await self._replace_chunks(
            document_id=document_id,
            job_id=job_id,
            lease_token=lease_token,
            chunks=chunks,
            embeddings=embeddings,
        )

    async def _checkpoint(
        self,
        *,
        document_id: UUID,
        job_id: UUID,
        lease_token: UUID,
        stage: str | None = None,
    ) -> Document:
        try:
            job = await self._session.scalar(
                select(DocumentJob)
                .where(DocumentJob.id == job_id)
                .execution_options(populate_existing=True)
            )
            document = await self._session.scalar(
                select(Document)
                .where(Document.id == document_id)
                .execution_options(populate_existing=True)
            )
            knowledge_base = None
            if document is not None:
                knowledge_base = await self._session.scalar(
                    select(KnowledgeBase)
                    .where(KnowledgeBase.id == document.knowledge_base_id)
                    .execution_options(populate_existing=True)
                )
            self._validate_resources(
                job=job,
                document=document,
                knowledge_base=knowledge_base,
                document_id=document_id,
                lease_token=lease_token,
                now=datetime.now(UTC),
            )
            assert job is not None and document is not None
            if stage is not None:
                stage_updated = await update_job_stage(
                    self._session,
                    job_id=job.id,
                    lease_token=lease_token,
                    stage=stage,
                    now=datetime.now(UTC),
                )
                if not stage_updated:
                    raise LeaseLostError("更新任务阶段时租约已失效")
                await self._session.commit()
            else:
                await self._session.rollback()
            return document
        except Exception:
            await self._session.rollback()
            raise

    async def _replace_chunks(
        self,
        *,
        document_id: UUID,
        job_id: UUID,
        lease_token: UUID,
        chunks: list[TextChunk],
        embeddings: list[list[float]],
    ) -> int:
        try:
            job = await self._session.scalar(
                select(DocumentJob)
                .where(DocumentJob.id == job_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            document = await self._session.scalar(
                select(Document)
                .where(Document.id == document_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            knowledge_base = None
            if document is not None:
                knowledge_base = await self._session.scalar(
                    select(KnowledgeBase)
                    .where(KnowledgeBase.id == document.knowledge_base_id)
                    .execution_options(populate_existing=True)
                )
            self._validate_resources(
                job=job,
                document=document,
                knowledge_base=knowledge_base,
                document_id=document_id,
                lease_token=lease_token,
                now=datetime.now(UTC),
            )
            assert job is not None and document is not None

            await self._session.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
            )
            self._session.add_all(
                [
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
                    for chunk, embedding in zip(chunks, embeddings, strict=True)
                ]
            )
            document.status = "ready"
            document.error_code = None
            document.error_message = None
            job.stage = "store"
            job.chunk_count = len(chunks)
            job.error_code = None
            job.error_message = None
            await self._session.commit()
            return len(chunks)
        except Exception:
            await self._session.rollback()
            raise

    @staticmethod
    def _validate_resources(
        *,
        job: DocumentJob | None,
        document: Document | None,
        knowledge_base: KnowledgeBase | None,
        document_id: UUID,
        lease_token: UUID,
        now: datetime,
    ) -> None:
        if (
            job is None
            or document is None
            or knowledge_base is None
            or job.job_type != "ingest_document"
            or job.resource_type != "document"
            or job.resource_id != document_id
            or job.knowledge_base_id != document.knowledge_base_id
            or job.owner_user_id != knowledge_base.owner_id
            or job.status != "processing"
            or job.lease_token != lease_token
            or job.lease_expires_at is None
            or job.lease_expires_at < now
        ):
            raise LeaseLostError("任务租约或文档资源已失效")

    def _stored_file_path(self, stored_file_name: str) -> Path:
        upload_root = self._upload_directory.resolve()
        file_path = (upload_root / stored_file_name).resolve()
        if not file_path.is_relative_to(upload_root):
            raise AppError(
                code="DOCUMENT_VALIDATION_FAILED",
                message="文档存储路径校验失败。",
                status_code=422,
            )
        return file_path

    def _validate_embeddings(self, *, chunks_count: int, embeddings: list[list[float]]) -> None:
        if len(embeddings) != chunks_count or any(
            len(embedding) != self._embedding_dimensions for embedding in embeddings
        ):
            raise AppError(
                code="EMBEDDING_PROVIDER_ERROR",
                message="Embedding 返回结果不符合入库要求。",
                status_code=502,
            )

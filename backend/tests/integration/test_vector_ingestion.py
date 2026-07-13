import os

import pytest
from sqlalchemy import select

from app.ai.embeddings import FakeEmbeddingProvider
from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.ingestion_job import IngestionJob
from app.db.models.knowledge_base import KnowledgeBase
from app.db.session import session_factory
from app.knowledge.chunking import RecursiveTextChunker
from app.knowledge.ingestion_service import IngestionService
from app.knowledge.parsers.registry import ParserRegistry
from app.knowledge.parsers.text import TextParser

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@pytest.mark.asyncio
async def test_process_stores_vectors_and_is_safe_to_retry(tmp_path) -> None:
    stored_name = "ingestion.txt"
    (tmp_path / stored_name).write_text("第一段制度内容。第二段制度内容。", encoding="utf-8")

    async with session_factory() as session:
        knowledge_base = KnowledgeBase(name="1C 向量入库测试")
        session.add(knowledge_base)
        await session.flush()
        document = Document(
            knowledge_base_id=knowledge_base.id,
            original_file_name="制度.txt",
            stored_file_name=stored_name,
            content_type="text/plain",
            file_extension=".txt",
            file_size=48,
            file_hash="a" * 64,
        )
        session.add(document)
        await session.flush()
        job = IngestionJob(document_id=document.id)
        session.add(job)
        await session.commit()

        service = IngestionService(
            session=session,
            upload_directory=tmp_path,
            parser_registry=ParserRegistry({".txt": TextParser()}),
            chunker=RecursiveTextChunker(chunk_size=10, chunk_overlap=2),
            embedding_provider=FakeEmbeddingProvider(dimensions=512),
            embedding_dimensions=512,
        )
        await service.process(document.id)
        await service.process(document.id)

        await session.refresh(document)
        await session.refresh(job)
        chunks = (
            await session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.document_id == document.id)
                .order_by(DocumentChunk.chunk_index)
            )
        ).all()

    assert document.status == "ready"
    assert job.status == "succeeded"
    assert job.chunk_count == len(chunks)
    assert len(chunks) > 1
    assert [chunk.chunk_index for chunk in chunks] == list(range(len(chunks)))
    assert all(len(chunk.embedding) == 512 for chunk in chunks)

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.ai.embeddings import FakeEmbeddingProvider
from app.core.security import hash_password
from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.document_job import DocumentJob
from app.db.models.knowledge_base import KnowledgeBase
from app.db.models.user import USER_ROLE, User
from app.db.session import session_factory
from app.jobs.repository import claim_next_job, complete_job
from app.knowledge.chunking import RecursiveTextChunker
from app.knowledge.ingestion_service import IngestionService
from app.knowledge.parsers.registry import ParserRegistry
from app.knowledge.parsers.text import TextParser
from app.knowledge.search_tokens import build_search_text

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@pytest.fixture
async def knowledge_base_owner() -> AsyncIterator[User]:
    user = User(
        id=uuid4(),
        username=f"vector_ingestion_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    async with session_factory.begin() as session:
        session.add(user)
    try:
        yield user
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(KnowledgeBase).where(KnowledgeBase.owner_id == user.id))
            await session.execute(delete(User).where(User.id == user.id))


@pytest.mark.asyncio
async def test_process_stores_vectors_and_is_safe_to_retry(
    tmp_path, knowledge_base_owner: User
) -> None:
    stored_name = "ingestion.txt"
    (tmp_path / stored_name).write_text("第一段制度内容。第二段制度内容。", encoding="utf-8")

    async with session_factory() as session:
        knowledge_base = KnowledgeBase(name="1C 向量入库测试", owner_id=knowledge_base_owner.id)
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
        job = DocumentJob(
            job_type="ingest_document",
            resource_type="document",
            resource_id=document.id,
            owner_user_id=knowledge_base_owner.id,
            knowledge_base_id=knowledge_base.id,
            stage="parse",
        )
        session.add(job)
        await session.commit()

        claim_at = datetime.now(UTC) + timedelta(seconds=1)
        lease = await claim_next_job(
            session,
            worker_id="vector-ingestion-test",
            now=claim_at,
            lease_seconds=120,
        )
        await session.commit()
        assert lease is not None

        service = IngestionService(
            session=session,
            upload_directory=tmp_path,
            parser_registry=ParserRegistry({".txt": TextParser()}),
            chunker=RecursiveTextChunker(chunk_size=10, chunk_overlap=2),
            embedding_provider=FakeEmbeddingProvider(dimensions=512),
            embedding_dimensions=512,
        )
        chunk_count = await service.process(
            document_id=document.id,
            job_id=job.id,
            lease_token=lease.lease_token,
        )
        await service.process(
            document_id=document.id,
            job_id=job.id,
            lease_token=lease.lease_token,
        )
        assert await complete_job(
            session,
            job_id=job.id,
            lease_token=lease.lease_token,
            chunk_count=chunk_count,
            now=claim_at + timedelta(seconds=1),
        )
        await session.commit()

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
    assert [chunk.search_text for chunk in chunks] == [
        build_search_text(chunk.content) for chunk in chunks
    ]

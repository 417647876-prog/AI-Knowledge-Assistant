import asyncio
import os
import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, delete, event, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.contracts import EmbeddingProvider
from app.core.config import Settings, get_settings
from app.db.models import Document, DocumentChunk, DocumentJob, KnowledgeBase, User
from app.jobs.repository import LeaseLostError, claim_next_job, complete_job, enqueue_job
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


@pytest.fixture(scope="module")
def recovery_database_url() -> Iterator[str]:
    configured_url = make_url(Settings().database_url)
    database_name = f"knowledge_ingestion_recovery_{uuid4().hex}"
    admin_engine = create_engine(
        configured_url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin_engine.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    database_url = configured_url.set(database=database_name).render_as_string(hide_password=False)
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = database_url
    get_settings.cache_clear()
    config = Config("alembic.ini")
    config.set_main_option("path_separator", "os")
    command.upgrade(config, "head")
    try:
        yield database_url
    finally:
        with admin_engine.connect() as connection:
            connection.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname=:database_name AND pid <> pg_backend_pid()"
                ),
                {"database_name": database_name},
            )
            connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        get_settings.cache_clear()


@pytest.fixture(scope="module")
async def recovery_session_factory(
    recovery_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(recovery_database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


class ConstantEmbeddingProvider:
    def __init__(self, value: float) -> None:
        self.value = value

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[self.value] * 512 for _ in texts]

    async def embed_query(self, text: str) -> list[float]:
        return (await self.embed_documents([text]))[0]


class BlockingEmbeddingProvider(ConstantEmbeddingProvider):
    def __init__(self, value: float) -> None:
        super().__init__(value)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.started.set()
        await self.release.wait()
        return await super().embed_documents(texts)


class WorkerStopped(RuntimeError):
    pass


class StoppingEmbeddingProvider(ConstantEmbeddingProvider):
    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        raise WorkerStopped("worker exited after parsing")


class SlowTextParser:
    def parse(self, file_path):
        time.sleep(0.3)
        return TextParser().parse(file_path)


async def _seed_and_claim(
    factory: async_sessionmaker[AsyncSession],
    upload_directory,
    *,
    lease_seconds: int = 120,
) -> tuple[Document, DocumentJob, object]:
    user = User(
        username=f"recovery_{uuid4().hex}",
        password_hash="hash",
        role="user",
        is_active=True,
    )
    stored_name = f"{uuid4()}.txt"
    (upload_directory / stored_name).write_text(
        "第一段制度内容。第二段制度内容。第三段制度内容。", encoding="utf-8"
    )
    now = datetime.now(UTC)
    async with factory.begin() as session:
        session.add(user)
        await session.flush()
        knowledge_base = KnowledgeBase(name=f"恢复测试-{uuid4()}", owner_id=user.id)
        session.add(knowledge_base)
        await session.flush()
        document = Document(
            knowledge_base_id=knowledge_base.id,
            original_file_name="制度.txt",
            stored_file_name=stored_name,
            content_type="text/plain",
            file_extension=".txt",
            file_size=60,
            file_hash=uuid4().hex * 2,
        )
        session.add(document)
        await session.flush()
        job = await enqueue_job(
            session,
            job_type="ingest_document",
            resource_type="document",
            resource_id=document.id,
            owner_user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            run_after=now - timedelta(seconds=1),
        )
    async with factory.begin() as session:
        lease = await claim_next_job(
            session,
            worker_id="worker-old",
            now=now,
            lease_seconds=lease_seconds,
        )
    assert lease is not None
    return document, job, lease


def _service(
    session: AsyncSession,
    upload_directory,
    provider: EmbeddingProvider,
) -> IngestionService:
    return IngestionService(
        session=session,
        upload_directory=upload_directory,
        parser_registry=ParserRegistry({".txt": TextParser()}),
        chunker=RecursiveTextChunker(chunk_size=10, chunk_overlap=2),
        embedding_provider=provider,
        embedding_dimensions=512,
    )


async def _process(
    factory: async_sessionmaker[AsyncSession],
    upload_directory,
    lease,
    provider: EmbeddingProvider,
) -> int:
    async with factory() as session:
        return await _service(session, upload_directory, provider).process(
            document_id=lease.resource_id,
            job_id=lease.job_id,
            lease_token=lease.lease_token,
        )


@pytest.mark.asyncio
async def test_expired_job_recovers_after_worker_stops_after_parsing(
    tmp_path, recovery_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    document, job, old_lease = await _seed_and_claim(
        recovery_session_factory, tmp_path, lease_seconds=1
    )

    with pytest.raises(WorkerStopped):
        await _process(
            recovery_session_factory,
            tmp_path,
            old_lease,
            StoppingEmbeddingProvider(0.1),
        )

    reclaim_at = old_lease.lease_expires_at + timedelta(microseconds=1)
    async with recovery_session_factory.begin() as session:
        new_lease = await claim_next_job(
            session,
            worker_id="worker-new",
            now=reclaim_at,
            lease_seconds=120,
        )
    assert new_lease is not None
    chunk_count = await _process(
        recovery_session_factory, tmp_path, new_lease, ConstantEmbeddingProvider(0.2)
    )
    async with recovery_session_factory.begin() as session:
        assert await complete_job(
            session,
            job_id=job.id,
            lease_token=new_lease.lease_token,
            chunk_count=chunk_count,
            now=reclaim_at + timedelta(seconds=1),
        )

    async with recovery_session_factory() as session:
        persisted_document = await session.get(Document, document.id)
        persisted_job = await session.get(DocumentJob, job.id)
        persisted_chunks = (
            await session.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
        ).all()
    assert persisted_document is not None and persisted_document.status == "ready"
    assert persisted_job is not None and persisted_job.status == "succeeded"
    assert len(persisted_chunks) == chunk_count


@pytest.mark.asyncio
async def test_repeated_execution_replaces_chunks_instead_of_duplicating(
    tmp_path, recovery_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    document, _job, lease = await _seed_and_claim(recovery_session_factory, tmp_path)

    first_count = await _process(
        recovery_session_factory, tmp_path, lease, ConstantEmbeddingProvider(0.3)
    )
    second_count = await _process(
        recovery_session_factory, tmp_path, lease, ConstantEmbeddingProvider(0.4)
    )

    async with recovery_session_factory() as session:
        stored_count = await session.scalar(
            select(func.count(DocumentChunk.id)).where(DocumentChunk.document_id == document.id)
        )
        embeddings = (
            await session.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
        ).all()
    assert first_count == second_count == stored_count
    assert embeddings and all(chunk.embedding[0] == pytest.approx(0.4) for chunk in embeddings)


@pytest.mark.asyncio
async def test_old_worker_cannot_commit_after_new_lease_finishes(
    tmp_path, recovery_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    document, job, old_lease = await _seed_and_claim(
        recovery_session_factory, tmp_path, lease_seconds=1
    )
    old_provider = BlockingEmbeddingProvider(0.5)
    old_task = asyncio.create_task(
        _process(recovery_session_factory, tmp_path, old_lease, old_provider)
    )
    await old_provider.started.wait()

    reclaim_at = old_lease.lease_expires_at + timedelta(microseconds=1)
    async with recovery_session_factory.begin() as session:
        new_lease = await claim_next_job(
            session,
            worker_id="worker-new",
            now=reclaim_at,
            lease_seconds=120,
        )
    assert new_lease is not None
    new_count = await _process(
        recovery_session_factory, tmp_path, new_lease, ConstantEmbeddingProvider(0.6)
    )
    async with recovery_session_factory.begin() as session:
        assert await complete_job(
            session,
            job_id=job.id,
            lease_token=new_lease.lease_token,
            chunk_count=new_count,
            now=reclaim_at + timedelta(seconds=1),
        )

    old_provider.release.set()
    with pytest.raises(LeaseLostError):
        await old_task

    async with recovery_session_factory() as session:
        persisted_job = await session.get(DocumentJob, job.id)
        chunks = (
            await session.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
        ).all()
    assert persisted_job is not None and persisted_job.status == "succeeded"
    assert len(chunks) == new_count
    assert all(chunk.embedding[0] == pytest.approx(0.6) for chunk in chunks)


@pytest.mark.asyncio
async def test_deleted_document_is_not_written_back_by_worker(
    tmp_path, recovery_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    document, _job, lease = await _seed_and_claim(recovery_session_factory, tmp_path)
    provider = BlockingEmbeddingProvider(0.7)
    processing = asyncio.create_task(_process(recovery_session_factory, tmp_path, lease, provider))
    await provider.started.wait()
    async with recovery_session_factory.begin() as session:
        await session.execute(delete(Document).where(Document.id == document.id))

    provider.release.set()
    with pytest.raises(LeaseLostError):
        await processing

    async with recovery_session_factory() as session:
        assert await session.get(Document, document.id) is None
        assert (
            await session.scalar(
                select(DocumentChunk.id).where(DocumentChunk.document_id == document.id)
            )
        ) is None


@pytest.mark.asyncio
async def test_document_status_tracks_blocked_parsing_and_embedding_stages(
    tmp_path, recovery_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    document, _job, lease = await _seed_and_claim(recovery_session_factory, tmp_path)
    provider = BlockingEmbeddingProvider(0.8)
    async with recovery_session_factory() as session:
        service = IngestionService(
            session=session,
            upload_directory=tmp_path,
            parser_registry=ParserRegistry({".txt": SlowTextParser()}),
            chunker=RecursiveTextChunker(chunk_size=10, chunk_overlap=2),
            embedding_provider=provider,
            embedding_dimensions=512,
        )
        processing = asyncio.create_task(
            service.process(
                document_id=document.id,
                job_id=lease.job_id,
                lease_token=lease.lease_token,
            )
        )
        await asyncio.sleep(0.05)
        async with recovery_session_factory() as observer:
            parsing_document = await observer.get(Document, document.id)
        assert parsing_document is not None and parsing_document.status == "parsing"

        await provider.started.wait()
        async with recovery_session_factory() as observer:
            embedding_document = await observer.get(Document, document.id)
        assert embedding_document is not None and embedding_document.status == "embedding"
        provider.release.set()
        await processing


@pytest.mark.asyncio
async def test_final_database_time_fence_rolls_back_after_lease_expires_during_flush(
    tmp_path, recovery_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    document, _job, lease = await _seed_and_claim(
        recovery_session_factory, tmp_path, lease_seconds=3
    )
    async with recovery_session_factory.begin() as session:
        persisted_document = await session.get(Document, document.id)
        assert persisted_document is not None
        persisted_document.status = "ready"
        session.add(
            DocumentChunk(
                document_id=document.id,
                knowledge_base_id=document.knowledge_base_id,
                chunk_index=0,
                content="old chunk",
                content_hash="f" * 64,
                extra_metadata={},
                embedding=[0.0] * 512,
                search_text="old chunk",
            )
        )

    engine = recovery_session_factory.kw["bind"]

    def expire_during_chunk_flush(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        if "INSERT INTO document_chunks" in statement:
            time.sleep(3.2)

    event.listen(engine.sync_engine, "before_cursor_execute", expire_during_chunk_flush)
    try:
        with pytest.raises(LeaseLostError):
            await _process(
                recovery_session_factory,
                tmp_path,
                lease,
                ConstantEmbeddingProvider(0.9),
            )
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", expire_during_chunk_flush)

    async with recovery_session_factory() as session:
        unchanged_document = await session.get(Document, document.id)
        chunks = (
            await session.scalars(
                select(DocumentChunk).where(DocumentChunk.document_id == document.id)
            )
        ).all()
    assert unchanged_document is not None and unchanged_document.status == "embedding"
    assert [chunk.content for chunk in chunks] == ["old chunk"]

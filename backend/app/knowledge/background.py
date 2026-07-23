import httpx

from app.ai.contracts import EmbeddingProvider
from app.ai.embeddings import (
    FakeEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    get_local_embedding_provider,
)
from app.core.config import Settings
from app.db.session import session_factory
from app.jobs.contracts import JobLease
from app.knowledge.chunking import RecursiveTextChunker
from app.knowledge.ingestion_service import IngestionService
from app.knowledge.parser_factory import create_parser_registry


async def _process_with_provider(
    lease: JobLease,
    settings: Settings,
    provider: EmbeddingProvider,
) -> int:
    async with session_factory() as session:
        service = IngestionService(
            session=session,
            upload_directory=settings.upload_directory,
            parser_registry=create_parser_registry(),
            chunker=RecursiveTextChunker(
                chunk_size=settings.chunk_size,
                chunk_overlap=settings.chunk_overlap,
            ),
            embedding_provider=provider,
            embedding_dimensions=settings.embedding_dimensions,
        )
        return await service.process(
            document_id=lease.resource_id,
            job_id=lease.job_id,
            lease_token=lease.lease_token,
        )


async def process_ingest_document(lease: JobLease, settings: Settings) -> int:
    if lease.job_type != "ingest_document" or lease.resource_type != "document":
        raise ValueError("process_ingest_document 仅处理 ingest_document 文档任务")

    if settings.embedding_provider == "fake":
        provider: EmbeddingProvider = FakeEmbeddingProvider(
            dimensions=settings.embedding_dimensions
        )
        return await _process_with_provider(lease, settings, provider)

    if settings.embedding_provider == "local":
        provider = get_local_embedding_provider(
            settings.embedding_model,
            settings.embedding_dimensions,
            settings.embedding_batch_size,
            settings.embedding_device,
        )
        return await _process_with_provider(lease, settings, provider)

    async with httpx.AsyncClient(timeout=30.0) as client:
        provider = OpenAICompatibleEmbeddingProvider(
            client=client,
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key or "",
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            batch_size=settings.embedding_batch_size,
        )
        return await _process_with_provider(lease, settings, provider)

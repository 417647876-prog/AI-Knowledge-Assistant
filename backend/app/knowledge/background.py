import logging
from uuid import UUID

import httpx

from app.ai.contracts import EmbeddingProvider
from app.ai.embeddings import (
    FakeEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    get_local_embedding_provider,
)
from app.core.config import Settings, get_settings
from app.db.session import session_factory
from app.knowledge.chunking import RecursiveTextChunker
from app.knowledge.ingestion_service import IngestionService
from app.knowledge.parser_factory import create_parser_registry

logger = logging.getLogger(__name__)


async def _process_with_provider(
    document_id: UUID, settings: Settings, provider: EmbeddingProvider
) -> None:
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
        await service.process(document_id)


async def run_ingestion(document_id: UUID) -> None:
    settings = get_settings()
    try:
        if settings.embedding_provider == "fake":
            provider: EmbeddingProvider = FakeEmbeddingProvider(
                dimensions=settings.embedding_dimensions
            )
            await _process_with_provider(document_id, settings, provider)
            return

        if settings.embedding_provider == "local":
            provider = get_local_embedding_provider(
                settings.embedding_model,
                settings.embedding_dimensions,
                settings.embedding_batch_size,
                settings.embedding_device,
            )
            await _process_with_provider(document_id, settings, provider)
            return

        async with httpx.AsyncClient(timeout=30.0) as client:
            provider = OpenAICompatibleEmbeddingProvider(
                client=client,
                base_url=settings.embedding_base_url,
                api_key=settings.embedding_api_key or "",
                model=settings.embedding_model,
                dimensions=settings.embedding_dimensions,
                batch_size=settings.embedding_batch_size,
            )
            await _process_with_provider(document_id, settings, provider)
    except Exception:
        logger.exception("文档后台入库失败", extra={"document_id": str(document_id)})

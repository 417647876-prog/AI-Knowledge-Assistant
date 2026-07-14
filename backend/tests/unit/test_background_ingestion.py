from uuid import uuid4

import pytest

from app.ai.embeddings import LocalEmbeddingProvider
from app.core.config import Settings
from app.knowledge import background


@pytest.mark.asyncio
async def test_background_ingestion_uses_local_embedding_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[object] = []
    settings = Settings(_env_file=None, embedding_provider="local")

    async def capture_provider(document_id, current_settings, provider) -> None:
        captured.append(provider)

    monkeypatch.setattr(background, "get_settings", lambda: settings)
    monkeypatch.setattr(background, "_process_with_provider", capture_provider)

    await background.run_ingestion(uuid4())
    await background.run_ingestion(uuid4())

    assert len(captured) == 2
    assert isinstance(captured[0], LocalEmbeddingProvider)
    assert captured[0] is captured[1]

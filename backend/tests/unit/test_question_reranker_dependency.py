from uuid import uuid4

import pytest

from app.ai.rerankers import FakeRerankerProvider
from app.api.v1 import questions
from app.core.config import Settings


def test_get_question_reranker_returns_none_when_disabled() -> None:
    settings = Settings(_env_file=None, rag_reranker_provider="disabled")

    assert questions.get_question_reranker(settings) is None


def test_get_question_reranker_returns_fake_provider() -> None:
    settings = Settings(_env_file=None, rag_reranker_provider="fake")

    assert isinstance(questions.get_question_reranker(settings), FakeRerankerProvider)


def test_get_question_reranker_builds_local_provider_from_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()
    received: dict[str, object] = {}

    def factory(model_name: str, device: str, batch_size: int) -> object:
        received.update(
            model_name=model_name,
            device=device,
            batch_size=batch_size,
        )
        return sentinel

    monkeypatch.setattr(questions, "get_local_reranker_provider", factory)
    settings = Settings(
        _env_file=None,
        rag_reranker_provider="local",
        rag_reranker_model="BAAI/test-reranker",
        rag_reranker_device="cpu",
        rag_reranker_batch_size=7,
    )

    result = questions.get_question_reranker(settings)

    assert result is sentinel
    assert received == {
        "model_name": "BAAI/test-reranker",
        "device": "cpu",
        "batch_size": 7,
    }


def test_get_question_reranker_reuses_local_provider_for_same_settings() -> None:
    settings = Settings(
        _env_file=None,
        rag_reranker_provider="local",
        rag_reranker_model="BAAI/cached-reranker",
        rag_reranker_device="cpu",
        rag_reranker_batch_size=7,
    )

    questions.get_local_reranker_provider.cache_clear()
    try:
        first = questions.get_question_reranker(settings)
        second = questions.get_question_reranker(settings)
    finally:
        questions.get_local_reranker_provider.cache_clear()

    assert first is second


@pytest.mark.asyncio
async def test_rag_service_factory_wires_reranker_settings_without_database() -> None:
    reranker = FakeRerankerProvider()
    service = await questions.get_rag_service(
        session=object(),
        current_user=type("FakeUser", (), {"id": uuid4()})(),
        embedding_provider=object(),
        chat_provider=object(),
        question_rewriter=object(),
        reranker=reranker,
        settings=Settings(
            _env_file=None,
            rag_candidate_k=12,
            rag_reranker_allow_fallback=False,
            rag_reranker_min_score=-0.25,
        ),
    )

    assert service._reranker is reranker
    assert service._candidate_k == 12
    assert service._reranker_allow_fallback is False
    assert service._reranker_min_score == -0.25

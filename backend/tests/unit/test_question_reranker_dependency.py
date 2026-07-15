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

    def factory(**kwargs: object) -> object:
        received.update(kwargs)
        return sentinel

    monkeypatch.setattr(questions, "LocalBgeRerankerProvider", factory)
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

from dataclasses import asdict
from decimal import Decimal
from uuid import uuid4

from app.conversations.service import StreamPersistenceState
from app.observations.service import ObservationMetrics, build_answer_observation
from app.rag.streaming import StreamEvent


def test_observation_maps_complete_metrics_without_sensitive_content() -> None:
    metrics = ObservationMetrics(
        was_rewritten=True,
        rewrite_fallback=False,
        candidate_count=4,
        accepted_scores=(0.9, 0.6),
        refused=False,
        citation_ids=(1, 2),
        rewrite_ms=11,
        retrieval_ms=22,
        generation_ms=33,
        total_ms=66,
        finish_reason="stop",
        error_code=None,
    )
    observation = build_answer_observation(
        user_id=uuid4(),
        knowledge_base_id=uuid4(),
        conversation_id=uuid4(),
        message_id=uuid4(),
        metrics=metrics,
    )

    assert observation.was_rewritten is True
    assert observation.rewrite_fallback is False
    assert observation.candidate_count == 4
    assert observation.accepted_count == 2
    assert observation.max_relevance == Decimal("0.9")
    assert observation.average_relevance == Decimal("0.75")
    assert observation.refused is False
    assert observation.citation_count == 2
    assert observation.citations_valid is True
    assert observation.rewrite_ms == 11
    assert observation.retrieval_ms == 22
    assert observation.generation_ms == 33
    assert observation.total_ms == 66
    assert observation.finish_reason == "stop"
    assert observation.error_code is None

    serialized = {
        column.name: getattr(observation, column.name)
        for column in observation.__table__.columns
        if column.name != "id"
    }
    serialized.update(asdict(metrics))
    serialized_text = repr(serialized).lower()
    for forbidden in ("question", "answer", "prompt", "content", "chunk", "file_name"):
        assert forbidden not in serialized_text


def test_observation_derives_unreferenced_and_empty_retrieval_generation_signals() -> None:
    metrics = ObservationMetrics(
        was_rewritten=False,
        rewrite_fallback=False,
        candidate_count=0,
        accepted_scores=(),
        refused=False,
        citation_ids=(),
        rewrite_ms=0,
        retrieval_ms=2,
        generation_ms=3,
        total_ms=5,
        finish_reason="stop",
        error_code=None,
    )

    assert metrics.direct_answer_without_citation is True
    assert metrics.generated_with_empty_retrieval is True


def test_observation_clamps_finite_reranker_scores_for_normalized_aggregates() -> None:
    metrics = ObservationMetrics(
        was_rewritten=False,
        rewrite_fallback=False,
        candidate_count=2,
        accepted_scores=(-0.25, 1.5),
        refused=False,
        citation_ids=(),
        rewrite_ms=1,
        retrieval_ms=2,
        generation_ms=3,
        total_ms=6,
        finish_reason="stop",
        error_code=None,
    )

    observation = build_answer_observation(
        user_id=uuid4(),
        knowledge_base_id=uuid4(),
        conversation_id=uuid4(),
        message_id=uuid4(),
        metrics=metrics,
    )

    assert observation.accepted_count == 2
    assert observation.max_relevance == Decimal("1")
    assert observation.average_relevance == Decimal("0.5")


def test_observation_ignores_non_finite_scores_defensively() -> None:
    metrics = ObservationMetrics(
        was_rewritten=False,
        rewrite_fallback=False,
        candidate_count=3,
        accepted_scores=(float("nan"), 0.4, float("inf")),
        refused=False,
        citation_ids=(),
        rewrite_ms=1,
        retrieval_ms=2,
        generation_ms=3,
        total_ms=6,
        finish_reason="stop",
        error_code=None,
    )

    observation = build_answer_observation(
        user_id=uuid4(),
        knowledge_base_id=uuid4(),
        conversation_id=uuid4(),
        message_id=uuid4(),
        metrics=metrics,
    )

    assert observation.accepted_count == 3
    assert observation.max_relevance == Decimal("0.4")
    assert observation.average_relevance == Decimal("0.4")

    all_non_finite = build_answer_observation(
        user_id=uuid4(),
        knowledge_base_id=uuid4(),
        conversation_id=uuid4(),
        message_id=uuid4(),
        metrics=ObservationMetrics(
            was_rewritten=False,
            rewrite_fallback=False,
            candidate_count=2,
            accepted_scores=(float("nan"), float("-inf")),
            refused=False,
            citation_ids=(),
            rewrite_ms=1,
            retrieval_ms=2,
            generation_ms=3,
            total_ms=6,
            finish_reason="stop",
            error_code=None,
        ),
    )

    assert all_non_finite.accepted_count == 2
    assert all_non_finite.max_relevance is None
    assert all_non_finite.average_relevance is None


def test_stream_state_exports_only_sanitized_observation_metrics() -> None:
    state = StreamPersistenceState()
    state.observe(
        StreamEvent(
            "rewrite",
            {"standalone_question": "敏感问题", "elapsed_ms": 7},
            persistence={"was_rewritten": True, "rewrite_fallback": False},
        )
    )
    state.observe(
        StreamEvent(
            "retrieval",
            {"retrieved_chunk_count": 2, "elapsed_ms": 8},
            persistence={"candidate_count": 5, "accepted_scores": (0.8, 0.4)},
        )
    )
    state.observe(StreamEvent("token", {"delta": "敏感答案"}))
    state.observe(
        StreamEvent(
            "done",
            {
                "citations": [
                    {
                        "citation_id": 1,
                        "file_name": "敏感文件.pdf",
                        "content": "敏感片段正文",
                    }
                ],
                "retrieved_chunk_count": 2,
                "timings": {
                    "rewrite_ms": 7,
                    "retrieval_ms": 8,
                    "generation_ms": 9,
                    "total_ms": 24,
                },
            },
            persistence={"refused": False, "finish_reason": "stop"},
        )
    )

    metrics = state.observation_metrics(error_code=None)

    assert metrics == ObservationMetrics(
        was_rewritten=True,
        rewrite_fallback=False,
        candidate_count=5,
        accepted_scores=(0.8, 0.4),
        refused=False,
        citation_ids=(1,),
        rewrite_ms=7,
        retrieval_ms=8,
        generation_ms=9,
        total_ms=24,
        finish_reason="stop",
        error_code=None,
    )
    serialized = repr(asdict(metrics))
    assert "敏感问题" not in serialized
    assert "敏感答案" not in serialized
    assert "敏感文件.pdf" not in serialized
    assert "敏感片段正文" not in serialized

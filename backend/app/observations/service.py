from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.db.models import AnswerObservation


@dataclass(frozen=True)
class ObservationMetrics:
    """不包含问题、答案、提示词、片段或文件信息的观测输入。"""

    was_rewritten: bool
    rewrite_fallback: bool
    candidate_count: int
    accepted_scores: tuple[float | None, ...]
    refused: bool
    generated_output: bool
    citation_ids: tuple[int, ...]
    rewrite_ms: int
    retrieval_ms: int
    generation_ms: int
    total_ms: int
    finish_reason: str | None
    error_code: str | None

    @property
    def direct_answer_without_citation(self) -> bool:
        return self.generated_output and not self.refused and not self.citation_ids

    @property
    def generated_with_empty_retrieval(self) -> bool:
        return self.generated_output and not self.refused and not self.accepted_scores


def build_answer_observation(
    *,
    user_id: UUID,
    knowledge_base_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    metrics: ObservationMetrics,
) -> AnswerObservation:
    scores = tuple(Decimal(str(score)) for score in metrics.accepted_scores if score is not None)
    normalized_scores = tuple(
        min(max(score, Decimal(0)), Decimal(1)) for score in scores if score.is_finite()
    )
    average_relevance = (
        sum(normalized_scores, start=Decimal(0)) / len(normalized_scores)
        if normalized_scores
        else None
    )
    citations_valid = all(
        1 <= citation_id <= len(metrics.accepted_scores) for citation_id in metrics.citation_ids
    )
    return AnswerObservation(
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        conversation_id=conversation_id,
        message_id=message_id,
        was_rewritten=metrics.was_rewritten,
        rewrite_fallback=metrics.rewrite_fallback,
        candidate_count=metrics.candidate_count,
        accepted_count=len(metrics.accepted_scores),
        max_relevance=max(normalized_scores, default=None),
        average_relevance=average_relevance,
        refused=metrics.refused,
        citation_count=len(metrics.citation_ids),
        citations_valid=citations_valid,
        rewrite_ms=metrics.rewrite_ms,
        retrieval_ms=metrics.retrieval_ms,
        generation_ms=metrics.generation_ms,
        total_ms=metrics.total_ms,
        finish_reason=metrics.finish_reason,
        error_code=metrics.error_code,
    )

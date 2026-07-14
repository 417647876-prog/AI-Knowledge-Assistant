import hashlib
import json
from collections.abc import Sequence
from statistics import fmean
from time import perf_counter
from typing import Literal, Protocol
from uuid import UUID

from app.ai.contracts import EmbeddingProvider
from app.evaluation.metrics import (
    citation_hit_rate,
    percentile,
    recall_at_k,
    reciprocal_rank_at_k,
    refusal_is_correct,
)
from app.evaluation.schemas import CaseResult, EvaluationCase, EvaluationReport
from app.rag.schemas import QuestionAnswer, RetrievedChunk

_METRIC_K = 5
EvaluationMode = Literal["vector", "hybrid", "rerank", "rewrite"]


class EvaluationRetriever(Protocol):
    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        query_embedding: list[float],
        top_k: int,
        score_threshold: float,
    ) -> list[RetrievedChunk]: ...


class EvaluationAnswerer(Protocol):
    async def answer_case(
        self, *, knowledge_base_id: UUID, case: EvaluationCase, top_k: int
    ) -> QuestionAnswer: ...


def _dataset_sha256(cases: Sequence[EvaluationCase]) -> str:
    canonical_json = json.dumps(
        [case.model_dump(mode="json") for case in cases],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


async def evaluate_cases(
    *,
    cases: list[EvaluationCase],
    knowledge_base_id: UUID,
    embedding_provider: EmbeddingProvider,
    retriever: EvaluationRetriever,
    answerer: EvaluationAnswerer,
    top_k: int,
    score_threshold: float,
    mode: EvaluationMode = "vector",
    environment: dict[str, str] | None = None,
) -> EvaluationReport:
    if not cases:
        raise ValueError("评估案例不能为空")
    if top_k < _METRIC_K:
        raise ValueError("top_k 不能小于 5，否则无法计算 Recall@5")

    results: list[CaseResult] = []
    citation_scores: list[float] = []

    for case in cases:
        started_at = perf_counter()
        query_embedding = await embedding_provider.embed_query(case.question)
        chunks = await retriever.search(
            knowledge_base_id=knowledge_base_id,
            query_embedding=query_embedding,
            top_k=top_k,
            score_threshold=score_threshold,
        )
        latency_ms = max(0.0, (perf_counter() - started_at) * 1000)

        answer = await answerer.answer_case(
            knowledge_base_id=knowledge_base_id,
            case=case,
            top_k=top_k,
        )
        refused = answer.retrieved_chunk_count == 0 and not answer.citations
        refusal_correct = refusal_is_correct(
            should_refuse=case.should_refuse,
            refused=refused,
        )
        citation_score = citation_hit_rate(case.expected_sources, answer.citations)
        citation_scores.append(citation_score)
        results.append(
            CaseResult(
                case_id=case.id,
                retrieved_files=[chunk.file_name for chunk in chunks],
                citation_files=[citation.file_name for citation in answer.citations],
                recall_at_k=recall_at_k(case.expected_sources, chunks, _METRIC_K),
                reciprocal_rank=reciprocal_rank_at_k(case.expected_sources, chunks, _METRIC_K),
                refused=refused,
                refusal_correct=refusal_correct,
                latency_ms=latency_ms,
            )
        )

    latencies = [result.latency_ms for result in results]
    return EvaluationReport(
        mode=mode,
        dataset_sha256=_dataset_sha256(cases),
        top_k=top_k,
        case_count=len(results),
        recall_at_5=fmean(result.recall_at_k for result in results),
        mrr_at_5=fmean(result.reciprocal_rank for result in results),
        citation_hit_rate=fmean(citation_scores),
        refusal_accuracy=fmean(float(result.refusal_correct) for result in results),
        latency_p50_ms=percentile(latencies, 0.5),
        latency_p95_ms=percentile(latencies, 0.95),
        environment=dict(environment or {}),
        cases=results,
    )

import math
from dataclasses import replace

from app.ai.contracts import RerankerProvider
from app.core.exceptions import AppError
from app.rag.schemas import RetrievedChunk


def _provider_error() -> AppError:
    return AppError(
        code="RERANKER_PROVIDER_ERROR",
        message="重排序服务返回了无效结果。",
        status_code=502,
    )


async def rerank_chunks(
    provider: RerankerProvider,
    *,
    query: str,
    chunks: list[RetrievedChunk],
    top_k: int,
) -> list[RetrievedChunk]:
    if top_k < 1 or top_k > len(chunks):
        raise ValueError("top_k 必须大于 0 且不超过候选片段数量")

    scores = await provider.rerank(query, [chunk.content for chunk in chunks])
    if len(scores) != len(chunks):
        raise _provider_error()

    try:
        normalized_scores = [float(score) for score in scores]
    except (TypeError, ValueError, OverflowError) as error:
        raise _provider_error() from error
    if not all(math.isfinite(score) for score in normalized_scores):
        raise _provider_error()

    scored_chunks = [
        (index, replace(chunk, relevance_score=score))
        for index, (chunk, score) in enumerate(zip(chunks, normalized_scores, strict=True))
    ]
    scored_chunks.sort(key=lambda item: (-item[1].relevance_score, item[0]))
    return [chunk for _, chunk in scored_chunks[:top_k]]

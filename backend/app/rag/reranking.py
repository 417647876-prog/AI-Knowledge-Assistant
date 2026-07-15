from dataclasses import replace

from app.ai.contracts import RerankerProvider
from app.core.exceptions import AppError
from app.rag.schemas import RetrievedChunk


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
        raise AppError(
            code="RERANKER_PROVIDER_ERROR",
            message="重排序服务返回的分数数量与候选片段数量不一致。",
            status_code=502,
        )

    scored_chunks = [
        (index, replace(chunk, relevance_score=score))
        for index, (chunk, score) in enumerate(zip(chunks, scores, strict=True))
    ]
    scored_chunks.sort(key=lambda item: (-item[1].relevance_score, item[0]))
    return [chunk for _, chunk in scored_chunks[:top_k]]

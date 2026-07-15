import logging
from collections.abc import AsyncIterator
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.contracts import (
    ConversationMessage,
    EmbeddingProvider,
    QuestionRewriter,
    RerankerProvider,
    StreamingChatProvider,
)
from app.ai.rewrite import should_rewrite
from app.core.exceptions import AppError
from app.core.request_context import get_request_id
from app.db.models.knowledge_base import KnowledgeBase
from app.rag.citations import map_citations
from app.rag.contracts import Retriever
from app.rag.prompt import build_rag_prompt
from app.rag.reranking import accept_reranked_chunks, rerank_chunks
from app.rag.schemas import QuestionAnswer, RetrievedChunk
from app.rag.streaming import CitationTracker, StreamEvent, citation_payload

NO_EVIDENCE_ANSWER = "未找到足够依据，无法根据当前知识库回答该问题。"
logger = logging.getLogger(__name__)


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


class RagService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        embedding_provider: EmbeddingProvider,
        retriever: Retriever,
        chat_provider: StreamingChatProvider,
        question_rewriter: QuestionRewriter,
        score_threshold: float,
        reranker: RerankerProvider | None = None,
        candidate_k: int = 20,
        reranker_allow_fallback: bool = True,
        reranker_min_score: float | None = None,
    ) -> None:
        self._session = session
        self._embedding_provider = embedding_provider
        self._retriever = retriever
        self._chat_provider = chat_provider
        self._question_rewriter = question_rewriter
        self._score_threshold = score_threshold
        self._reranker = reranker
        self._candidate_k = candidate_k
        self._reranker_allow_fallback = reranker_allow_fallback
        self._reranker_min_score = reranker_min_score

    async def _ensure_knowledge_base(self, knowledge_base_id: UUID) -> None:
        if await self._session.get(KnowledgeBase, knowledge_base_id) is None:
            raise AppError(
                code="KNOWLEDGE_BASE_NOT_FOUND",
                message="知识库不存在。",
                status_code=404,
            )

    async def _retrieve(self, knowledge_base_id: UUID, question: str, top_k: int):
        query_embedding = await self._embedding_provider.embed_query(question)
        retrieval_top_k = max(top_k, self._candidate_k) if self._reranker is not None else top_k
        chunks = await self._retriever.search(
            knowledge_base_id=knowledge_base_id,
            query=question,
            query_embedding=query_embedding,
            top_k=retrieval_top_k,
            score_threshold=self._score_threshold,
        )
        if self._reranker is None or not chunks:
            return chunks

        try:
            reranked_chunks = await rerank_chunks(
                self._reranker,
                query=question,
                chunks=chunks,
                top_k=min(top_k, len(chunks)),
            )
        except AppError as error:
            if error.code != "RERANKER_PROVIDER_ERROR" or not self._reranker_allow_fallback:
                raise
            logger.warning(
                "Reranker 调用失败，已按配置回退。",
                extra={
                    "error_code": error.code,
                    "reranker_provider": type(self._reranker).__name__,
                    "request_id": get_request_id(),
                },
            )
            return chunks[:top_k]

        accepted_chunks = accept_reranked_chunks(
            reranked_chunks,
            min_score=self._reranker_min_score,
        )
        if len(accepted_chunks) < len(reranked_chunks):
            logger.info(
                "Reranker 接受门已过滤低分候选。",
                extra={
                    "reranker_provider": type(self._reranker).__name__,
                    "reranker_min_score": self._reranker_min_score,
                    "candidate_count": len(reranked_chunks),
                    "accepted_count": len(accepted_chunks),
                    "rejected_count": len(reranked_chunks) - len(accepted_chunks),
                    "request_id": get_request_id(),
                },
            )
        return accepted_chunks

    async def _answer_from_chunks(
        self, question: str, chunks: list[RetrievedChunk]
    ) -> QuestionAnswer:
        if not chunks:
            return QuestionAnswer(
                answer=NO_EVIDENCE_ANSWER,
                citations=[],
                retrieved_chunk_count=0,
            )
        system_prompt, user_prompt = build_rag_prompt(question, chunks)
        answer = await self._chat_provider.generate(system_prompt, user_prompt)
        return QuestionAnswer(
            answer=answer,
            citations=map_citations(answer, chunks),
            retrieved_chunk_count=len(chunks),
        )

    async def answer_with_retrieval(
        self, knowledge_base_id: UUID, question: str, top_k: int
    ) -> tuple[QuestionAnswer, list[RetrievedChunk], float]:
        await self._ensure_knowledge_base(knowledge_base_id)
        question = question.strip()
        retrieval_started = perf_counter()
        chunks = await self._retrieve(knowledge_base_id, question, top_k)
        retrieval_latency_ms = max(0.0, (perf_counter() - retrieval_started) * 1000)
        answer = await self._answer_from_chunks(question, chunks)
        return answer, chunks, retrieval_latency_ms

    async def answer_with_retrieval_question(
        self,
        knowledge_base_id: UUID,
        original_question: str,
        retrieval_question: str,
        top_k: int,
    ) -> QuestionAnswer:
        await self._ensure_knowledge_base(knowledge_base_id)
        original_question = original_question.strip()
        retrieval_question = retrieval_question.strip()
        chunks = await self._retrieve(knowledge_base_id, retrieval_question, top_k)
        return await self._answer_from_chunks(original_question, chunks)

    async def answer(self, knowledge_base_id: UUID, question: str, top_k: int) -> QuestionAnswer:
        return await self.answer_with_retrieval_question(
            knowledge_base_id,
            original_question=question,
            retrieval_question=question,
            top_k=top_k,
        )

    async def stream_answer(
        self,
        knowledge_base_id: UUID,
        question: str,
        top_k: int,
        history: list[ConversationMessage],
    ) -> AsyncIterator[StreamEvent]:
        total_started = perf_counter()
        await self._ensure_knowledge_base(knowledge_base_id)

        original_question = question.strip()
        standalone_question = original_question
        used_fallback = False
        rewrite_ms = 0

        if should_rewrite(original_question, history):
            yield StreamEvent("status", {"phase": "rewriting"})
            rewrite_started = perf_counter()
            try:
                standalone_question = await self._question_rewriter.rewrite(
                    history,
                    original_question,
                )
            except AppError as error:
                if error.code != "QUESTION_REWRITE_ERROR":
                    raise
                used_fallback = True
            rewrite_ms = _elapsed_ms(rewrite_started)
        yield StreamEvent(
            "rewrite",
            {
                "standalone_question": standalone_question,
                "elapsed_ms": rewrite_ms,
                "used_fallback": used_fallback,
            },
        )

        yield StreamEvent("status", {"phase": "retrieving"})
        retrieval_started = perf_counter()
        chunks = await self._retrieve(knowledge_base_id, standalone_question, top_k)
        retrieval_ms = _elapsed_ms(retrieval_started)
        yield StreamEvent(
            "retrieval",
            {"retrieved_chunk_count": len(chunks), "elapsed_ms": retrieval_ms},
        )

        if not chunks:
            yield StreamEvent("token", {"delta": NO_EVIDENCE_ANSWER})
            yield StreamEvent(
                "done",
                {
                    "citations": [],
                    "retrieved_chunk_count": 0,
                    "timings": {
                        "rewrite_ms": rewrite_ms,
                        "retrieval_ms": retrieval_ms,
                        "generation_ms": 0,
                        "total_ms": _elapsed_ms(total_started),
                    },
                },
            )
            return

        yield StreamEvent("status", {"phase": "generating"})
        system_prompt, user_prompt = build_rag_prompt(original_question, chunks)
        tracker = CitationTracker(chunks)
        generation_started = perf_counter()
        chat_stream = self._chat_provider.stream(system_prompt, user_prompt)
        try:
            async for delta in chat_stream:
                yield StreamEvent("token", {"delta": delta})
                for citation in tracker.feed(delta):
                    yield StreamEvent("citation", citation_payload(citation))
        finally:
            close = getattr(chat_stream, "aclose", None)
            if close is not None:
                await close()
        generation_ms = _elapsed_ms(generation_started)
        yield StreamEvent(
            "done",
            {
                "citations": [citation_payload(item) for item in tracker.finish()],
                "retrieved_chunk_count": len(chunks),
                "timings": {
                    "rewrite_ms": rewrite_ms,
                    "retrieval_ms": retrieval_ms,
                    "generation_ms": generation_ms,
                    "total_ms": _elapsed_ms(total_started),
                },
            },
        )

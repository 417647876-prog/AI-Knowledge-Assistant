from collections.abc import AsyncIterator
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.contracts import (
    ConversationMessage,
    EmbeddingProvider,
    QuestionRewriter,
    StreamingChatProvider,
)
from app.core.exceptions import AppError
from app.db.models.knowledge_base import KnowledgeBase
from app.rag.citations import map_citations
from app.rag.contracts import Retriever
from app.rag.prompt import build_rag_prompt
from app.rag.schemas import QuestionAnswer
from app.rag.streaming import CitationTracker, StreamEvent, citation_payload

NO_EVIDENCE_ANSWER = "未找到足够依据，无法根据当前知识库回答该问题。"


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
    ) -> None:
        self._session = session
        self._embedding_provider = embedding_provider
        self._retriever = retriever
        self._chat_provider = chat_provider
        self._question_rewriter = question_rewriter
        self._score_threshold = score_threshold

    async def _ensure_knowledge_base(self, knowledge_base_id: UUID) -> None:
        if await self._session.get(KnowledgeBase, knowledge_base_id) is None:
            raise AppError(
                code="KNOWLEDGE_BASE_NOT_FOUND",
                message="知识库不存在。",
                status_code=404,
            )

    async def _retrieve(self, knowledge_base_id: UUID, question: str, top_k: int):
        query_embedding = await self._embedding_provider.embed_query(question)
        return await self._retriever.search(
            knowledge_base_id=knowledge_base_id,
            query=question,
            query_embedding=query_embedding,
            top_k=top_k,
            score_threshold=self._score_threshold,
        )

    async def answer(self, knowledge_base_id: UUID, question: str, top_k: int) -> QuestionAnswer:
        await self._ensure_knowledge_base(knowledge_base_id)
        question = question.strip()
        chunks = await self._retrieve(knowledge_base_id, question, top_k)
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

    async def stream_answer(
        self,
        knowledge_base_id: UUID,
        question: str,
        top_k: int,
        history: list[ConversationMessage],
    ) -> AsyncIterator[StreamEvent]:
        total_started = perf_counter()
        await self._ensure_knowledge_base(knowledge_base_id)

        if history:
            yield StreamEvent("status", {"phase": "rewriting"})
            rewrite_started = perf_counter()
            standalone = await self._question_rewriter.rewrite(history, question)
            rewrite_ms = _elapsed_ms(rewrite_started)
        else:
            standalone = question.strip()
            rewrite_ms = 0
        yield StreamEvent(
            "rewrite",
            {"standalone_question": standalone, "elapsed_ms": rewrite_ms},
        )

        yield StreamEvent("status", {"phase": "retrieving"})
        retrieval_started = perf_counter()
        chunks = await self._retrieve(knowledge_base_id, standalone, top_k)
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
        system_prompt, user_prompt = build_rag_prompt(standalone, chunks)
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

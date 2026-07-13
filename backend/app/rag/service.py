from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.contracts import ChatProvider, EmbeddingProvider
from app.core.exceptions import AppError
from app.db.models.knowledge_base import KnowledgeBase
from app.rag.citations import map_citations
from app.rag.prompt import build_rag_prompt
from app.rag.retriever import VectorRetriever
from app.rag.schemas import QuestionAnswer

NO_EVIDENCE_ANSWER = "未找到足够依据，无法根据当前知识库回答该问题。"


class RagService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        embedding_provider: EmbeddingProvider,
        retriever: VectorRetriever,
        chat_provider: ChatProvider,
        score_threshold: float,
    ) -> None:
        self._session = session
        self._embedding_provider = embedding_provider
        self._retriever = retriever
        self._chat_provider = chat_provider
        self._score_threshold = score_threshold

    async def answer(self, knowledge_base_id: UUID, question: str, top_k: int) -> QuestionAnswer:
        if await self._session.get(KnowledgeBase, knowledge_base_id) is None:
            raise AppError(
                code="KNOWLEDGE_BASE_NOT_FOUND",
                message="知识库不存在。",
                status_code=404,
            )
        query_embedding = await self._embedding_provider.embed_query(question.strip())
        chunks = await self._retriever.search(
            knowledge_base_id=knowledge_base_id,
            query_embedding=query_embedding,
            top_k=top_k,
            score_threshold=self._score_threshold,
        )
        if not chunks:
            return QuestionAnswer(
                answer=NO_EVIDENCE_ANSWER,
                citations=[],
                retrieved_chunk_count=0,
            )
        system_prompt, user_prompt = build_rag_prompt(question.strip(), chunks)
        answer = await self._chat_provider.generate(system_prompt, user_prompt)
        return QuestionAnswer(
            answer=answer,
            citations=map_citations(answer, chunks),
            retrieved_chunk_count=len(chunks),
        )

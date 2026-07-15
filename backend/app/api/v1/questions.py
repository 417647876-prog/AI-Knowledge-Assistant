from collections.abc import AsyncIterator
from typing import Annotated, Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.chat import FakeChatProvider, OpenAICompatibleChatProvider
from app.ai.contracts import (
    ConversationMessage,
    EmbeddingProvider,
    QuestionRewriter,
    RerankerProvider,
    StreamingChatProvider,
)
from app.ai.embeddings import (
    FakeEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    get_local_embedding_provider,
)
from app.ai.rerankers import FakeRerankerProvider, get_local_reranker_provider
from app.ai.rewrite import ChatQuestionRewriter, FakeQuestionRewriter
from app.api.auth_dependencies import get_current_user
from app.api.sse import iter_sse
from app.authorization.service import get_owned_knowledge_base
from app.core.config import Settings, get_settings
from app.db.models import User
from app.db.session import get_session
from app.rag.contracts import Retriever
from app.rag.hybrid_retriever import HybridRetriever
from app.rag.keyword_retriever import KeywordRetriever
from app.rag.retriever import VectorRetriever
from app.rag.service import RagService

router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["questions"])


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=20)

    @field_validator("question")
    @classmethod
    def strip_and_reject_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


class ConversationMessageRequest(BaseModel):
    role: Literal["user", "assistant"]
    content: str

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str, info) -> str:
        value = value.strip()
        limit = 2000 if info.data.get("role") == "user" else 8000
        if not value or len(value) > limit:
            raise ValueError("历史消息内容长度不合法")
        return value


class StreamQuestionRequest(QuestionRequest):
    history: list[ConversationMessageRequest] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def validate_history_pairs(self):
        expected = "user"
        for message in self.history:
            if message.role != expected:
                raise ValueError("历史消息必须严格按照 user 和 assistant 成对排列")
            expected = "assistant" if expected == "user" else "user"
        if expected == "assistant":
            raise ValueError("历史消息必须以完整问答对结束")
        return self


class CitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    citation_id: int
    document_id: UUID
    file_name: str
    content: str
    relevance_score: float
    page_number: int | None
    sheet_name: str | None
    row_start: int | None
    section_title: str | None


class QuestionResponse(BaseModel):
    answer: str
    citations: list[CitationResponse]
    retrieved_chunk_count: int
    request_id: str


async def get_question_embedding_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[EmbeddingProvider]:
    if settings.embedding_provider == "fake":
        yield FakeEmbeddingProvider(dimensions=settings.embedding_dimensions)
        return
    if settings.embedding_provider == "local":
        yield get_local_embedding_provider(
            settings.embedding_model,
            settings.embedding_dimensions,
            settings.embedding_batch_size,
            settings.embedding_device,
        )
        return
    async with httpx.AsyncClient(timeout=30.0) as client:
        yield OpenAICompatibleEmbeddingProvider(
            client=client,
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key or "",
            model=settings.embedding_model,
            dimensions=settings.embedding_dimensions,
            batch_size=settings.embedding_batch_size,
        )


async def get_question_chat_provider(
    settings: Annotated[Settings, Depends(get_settings)],
) -> AsyncIterator[StreamingChatProvider]:
    if settings.chat_provider == "fake":
        yield FakeChatProvider()
        return
    async with httpx.AsyncClient(timeout=settings.chat_timeout_seconds) as client:
        yield OpenAICompatibleChatProvider(
            client=client,
            base_url=settings.chat_base_url,
            api_key=settings.chat_api_key or "",
            model=settings.chat_model,
        )


async def get_question_rewriter(
    settings: Annotated[Settings, Depends(get_settings)],
    chat_provider: Annotated[StreamingChatProvider, Depends(get_question_chat_provider)],
) -> QuestionRewriter:
    if settings.chat_provider == "fake":
        return FakeQuestionRewriter()
    return ChatQuestionRewriter(chat_provider)


def get_question_reranker(
    settings: Annotated[Settings, Depends(get_settings)],
) -> RerankerProvider | None:
    if settings.rag_reranker_provider == "disabled":
        return None
    if settings.rag_reranker_provider == "fake":
        return FakeRerankerProvider()
    return get_local_reranker_provider(
        settings.rag_reranker_model,
        settings.rag_reranker_device,
        settings.rag_reranker_batch_size,
    )


def build_retriever(session: AsyncSession, settings: Settings) -> Retriever:
    vector = VectorRetriever(session)
    if settings.rag_retrieval_mode == "vector":
        return vector
    return HybridRetriever(
        vector,
        KeywordRetriever(session),
        rank_constant=settings.rag_rrf_rank_constant,
    )


async def get_rag_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    embedding_provider: Annotated[EmbeddingProvider, Depends(get_question_embedding_provider)],
    chat_provider: Annotated[StreamingChatProvider, Depends(get_question_chat_provider)],
    question_rewriter: Annotated[QuestionRewriter, Depends(get_question_rewriter)],
    settings: Annotated[Settings, Depends(get_settings)],
    reranker: Annotated[RerankerProvider | None, Depends(get_question_reranker)] = None,
) -> RagService:
    return RagService(
        session=session,
        owner_user_id=current_user.id,
        embedding_provider=embedding_provider,
        retriever=build_retriever(session, settings),
        chat_provider=chat_provider,
        question_rewriter=question_rewriter,
        score_threshold=settings.rag_score_threshold,
        reranker=reranker,
        candidate_k=settings.rag_candidate_k,
        reranker_allow_fallback=settings.rag_reranker_allow_fallback,
        reranker_min_score=settings.rag_reranker_min_score,
    )


@router.post("/{knowledge_base_id}/questions", response_model=QuestionResponse)
async def ask_question(
    knowledge_base_id: UUID,
    payload: QuestionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RagService, Depends(get_rag_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> QuestionResponse:
    await get_owned_knowledge_base(session, current_user, knowledge_base_id)
    top_k = payload.top_k or settings.rag_top_k_default
    result = await service.answer(knowledge_base_id, payload.question, top_k)
    return QuestionResponse(
        answer=result.answer,
        citations=[CitationResponse.model_validate(item) for item in result.citations],
        retrieved_chunk_count=result.retrieved_chunk_count,
        request_id=request.state.request_id,
    )


@router.post("/{knowledge_base_id}/questions/stream")
async def stream_question(
    knowledge_base_id: UUID,
    payload: StreamQuestionRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RagService, Depends(get_rag_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> StreamingResponse:
    await get_owned_knowledge_base(session, current_user, knowledge_base_id)
    top_k = payload.top_k or settings.rag_top_k_default
    history = [
        ConversationMessage(role=item.role, content=item.content) for item in payload.history
    ]
    source = service.stream_answer(knowledge_base_id, payload.question, top_k, history)
    return StreamingResponse(
        iter_sse(request, source, request.state.request_id),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

from collections.abc import AsyncIterator
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.chat import FakeChatProvider, OpenAICompatibleChatProvider
from app.ai.contracts import ChatProvider, EmbeddingProvider
from app.ai.embeddings import (
    FakeEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    get_local_embedding_provider,
)
from app.core.config import Settings, get_settings
from app.db.session import get_session
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
) -> AsyncIterator[ChatProvider]:
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


async def get_rag_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    embedding_provider: Annotated[
        EmbeddingProvider, Depends(get_question_embedding_provider)
    ],
    chat_provider: Annotated[ChatProvider, Depends(get_question_chat_provider)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RagService:
    return RagService(
        session=session,
        embedding_provider=embedding_provider,
        retriever=VectorRetriever(session),
        chat_provider=chat_provider,
        score_threshold=settings.rag_score_threshold,
    )


@router.post("/{knowledge_base_id}/questions", response_model=QuestionResponse)
async def ask_question(
    knowledge_base_id: UUID,
    payload: QuestionRequest,
    request: Request,
    service: Annotated[RagService, Depends(get_rag_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> QuestionResponse:
    top_k = payload.top_k or settings.rag_top_k_default
    result = await service.answer(knowledge_base_id, payload.question, top_k)
    return QuestionResponse(
        answer=result.answer,
        citations=[CitationResponse.model_validate(item) for item in result.citations],
        retrieved_chunk_count=result.retrieved_chunk_count,
        request_id=request.state.request_id,
    )

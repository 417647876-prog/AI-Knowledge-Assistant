from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import get_current_user
from app.api.sse import DisconnectAwareStreamingResponse, iter_sse
from app.api.v1.questions import get_rag_service
from app.conversations.schemas import (
    ConversationDetail,
    ConversationMessageResponse,
    ConversationPage,
    ConversationSummary,
    CreateConversationRequest,
    StreamConversationMessageRequest,
)
from app.conversations.service import (
    StreamPersistenceState,
    create_conversation,
    delete_conversation_body,
    finalize_conversation_stream,
    get_conversation_messages,
    get_owned_conversation,
    list_conversations,
    prepare_conversation_stream,
)
from app.core.config import Settings, get_settings
from app.db.models import User
from app.db.session import get_session, session_factory
from app.rag.service import RagService
from app.usage.pricing import ModelPricing
from app.usage.service import ConversationUsageRecorder

router = APIRouter(prefix="/api/v1", tags=["conversations"])


async def get_conversation_rag_service(
    service: Annotated[RagService, Depends(get_rag_service)],
) -> RagService:
    return service


@router.post(
    "/knowledge-bases/{knowledge_base_id}/conversations",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
)
async def create_conversation_endpoint(
    knowledge_base_id: UUID,
    payload: CreateConversationRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConversationSummary:
    conversation = await create_conversation(
        session,
        user_id=current_user.id,
        knowledge_base_id=knowledge_base_id,
        title=payload.title,
    )
    await session.commit()
    return ConversationSummary.model_validate(conversation)


@router.get(
    "/knowledge-bases/{knowledge_base_id}/conversations",
    response_model=ConversationPage,
)
async def list_conversations_endpoint(
    knowledge_base_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> ConversationPage:
    result = await list_conversations(
        session,
        user_id=current_user.id,
        knowledge_base_id=knowledge_base_id,
        page=page,
        page_size=page_size,
    )
    return ConversationPage(
        items=[ConversationSummary.model_validate(item) for item in result.items],
        page=page,
        page_size=page_size,
        total=result.total,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation_endpoint(
    conversation_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ConversationDetail:
    conversation = await get_owned_conversation(
        session,
        user_id=current_user.id,
        conversation_id=conversation_id,
    )
    messages = await get_conversation_messages(session, conversation_id=conversation.id)
    summary = ConversationSummary.model_validate(conversation)
    return ConversationDetail(
        **summary.model_dump(),
        messages=[ConversationMessageResponse.model_validate(item) for item in messages],
    )


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_conversation_endpoint(
    conversation_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    await delete_conversation_body(
        session,
        user_id=current_user.id,
        conversation_id=conversation_id,
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/conversations/{conversation_id}/messages/stream")
async def stream_conversation_message(
    conversation_id: UUID,
    payload: StreamConversationMessageRequest,
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
    service: Annotated[RagService, Depends(get_conversation_rag_service)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> DisconnectAwareStreamingResponse:
    pricing = ModelPricing(
        cache_hit_input_per_million=settings.chat_cache_hit_input_price_per_million,
        cache_miss_input_per_million=settings.chat_cache_miss_input_price_per_million,
        output_per_million=settings.chat_output_price_per_million,
    )
    top_k = payload.top_k or settings.rag_top_k_default
    prepared = await prepare_conversation_stream(
        session,
        user_id=current_user.id,
        conversation_id=conversation_id,
        question=payload.question,
        retry_of_message_id=payload.retry_of_message_id,
        model=settings.chat_model,
        pricing=pricing,
        answer_input_tokens=settings.chat_answer_input_token_reserve,
        answer_max_output_tokens=settings.chat_answer_max_output_tokens,
        answer_top_k=top_k,
        chunk_size=settings.chunk_size,
    )
    await session.commit()
    state = StreamPersistenceState()
    usage_recorder = ConversationUsageRecorder(
        session_factory=session_factory,
        user_id=current_user.id,
        knowledge_base_id=prepared.knowledge_base_id,
        conversation_id=prepared.conversation_id,
        message_id=prepared.assistant_message_id,
        model=settings.chat_model,
        pricing=pricing,
        rewrite_input_tokens=settings.chat_rewrite_input_token_reserve,
        rewrite_max_output_tokens=settings.chat_rewrite_max_output_tokens,
        answer_usage_id=prepared.answer_usage_id,
        answer_max_output_tokens=settings.chat_answer_max_output_tokens,
    )

    async def finalize(outcome, error_code: str | None) -> None:
        try:
            async with session_factory.begin() as final_session:
                await finalize_conversation_stream(
                    final_session,
                    prepared=prepared,
                    state=state,
                    outcome=outcome,
                    pricing=pricing,
                    error_code=error_code,
                )
        except Exception:
            recovery_outcome = "provider_failed" if outcome == "completed" else outcome
            async with session_factory.begin() as failure_session:
                await finalize_conversation_stream(
                    failure_session,
                    prepared=prepared,
                    state=state,
                    outcome=recovery_outcome,
                    pricing=pricing,
                    error_code="PERSISTENCE_ERROR",
                    record_observation=False,
                )
            raise

    source = service.stream_answer(
        prepared.knowledge_base_id,
        prepared.question,
        top_k,
        prepared.history,
        usage_recorder=usage_recorder,
    )
    return DisconnectAwareStreamingResponse(
        iter_sse(
            request,
            source,
            request.state.request_id,
            on_event=state.observe,
            on_finalize=finalize,
        ),
        media_type="text/event-stream; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.contracts import ChatUsage
from app.ai.contracts import ConversationMessage as PromptMessage
from app.core.exceptions import AppError
from app.db.models import (
    AnswerFeedback,
    AnswerObservation,
    Conversation,
    ConversationMessage,
    KnowledgeBase,
    LlmUsageEvent,
)
from app.usage.pricing import ModelPricing, calculate_reservation
from app.usage.service import (
    apply_settlement,
    create_usage_reservation,
    settle_after_failure,
    settle_after_response,
)


def _not_found() -> AppError:
    return AppError(
        code="CONVERSATION_NOT_FOUND",
        message="会话不存在。",
        status_code=404,
    )


async def require_owned_active_knowledge_base(
    session: AsyncSession,
    *,
    user_id: UUID,
    knowledge_base_id: UUID,
) -> KnowledgeBase:
    knowledge_base = await session.scalar(
        select(KnowledgeBase).where(
            KnowledgeBase.id == knowledge_base_id,
            KnowledgeBase.owner_id == user_id,
            KnowledgeBase.deleted_at.is_(None),
        )
    )
    if knowledge_base is None:
        raise AppError(
            code="KNOWLEDGE_BASE_NOT_FOUND",
            message="知识库不存在。",
            status_code=404,
        )
    return knowledge_base


async def create_conversation(
    session: AsyncSession,
    *,
    user_id: UUID,
    knowledge_base_id: UUID,
    title: str,
) -> Conversation:
    await require_owned_active_knowledge_base(
        session,
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
    )
    conversation = Conversation(
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
        title=title,
    )
    session.add(conversation)
    await session.flush()
    return conversation


@dataclass(frozen=True)
class ConversationPageResult:
    items: list[Conversation]
    total: int


async def list_conversations(
    session: AsyncSession,
    *,
    user_id: UUID,
    knowledge_base_id: UUID,
    page: int,
    page_size: int,
) -> ConversationPageResult:
    await require_owned_active_knowledge_base(
        session,
        user_id=user_id,
        knowledge_base_id=knowledge_base_id,
    )
    filters = (
        Conversation.user_id == user_id,
        Conversation.knowledge_base_id == knowledge_base_id,
    )
    total = await session.scalar(select(func.count()).select_from(Conversation).where(*filters))
    items = list(
        (
            await session.scalars(
                select(Conversation)
                .where(*filters)
                .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()
    )
    return ConversationPageResult(items=items, total=int(total or 0))


async def get_owned_conversation(
    session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    for_update: bool = False,
) -> Conversation:
    statement = (
        select(Conversation)
        .join(KnowledgeBase, KnowledgeBase.id == Conversation.knowledge_base_id)
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            KnowledgeBase.owner_id == user_id,
            KnowledgeBase.deleted_at.is_(None),
        )
    )
    if for_update:
        statement = statement.with_for_update(of=Conversation)
    conversation = await session.scalar(statement)
    if conversation is None:
        raise _not_found()
    return conversation


async def get_conversation_messages(
    session: AsyncSession,
    *,
    conversation_id: UUID,
) -> list[ConversationMessage]:
    return list(
        (
            await session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation_id)
                .order_by(ConversationMessage.sequence_number)
            )
        ).all()
    )


async def delete_conversation_body(
    session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
) -> None:
    conversation = await get_owned_conversation(
        session,
        user_id=user_id,
        conversation_id=conversation_id,
        for_update=True,
    )
    message_ids = select(ConversationMessage.id).where(
        ConversationMessage.conversation_id == conversation.id
    )
    await session.execute(delete(AnswerFeedback).where(AnswerFeedback.message_id.in_(message_ids)))
    await session.execute(
        delete(AnswerObservation).where(AnswerObservation.conversation_id == conversation.id)
    )
    await session.execute(
        delete(ConversationMessage).where(ConversationMessage.conversation_id == conversation.id)
    )
    await session.delete(conversation)


@dataclass(frozen=True)
class PreparedConversationStream:
    conversation_id: UUID
    knowledge_base_id: UUID
    assistant_message_id: UUID
    answer_usage_id: UUID
    question: str
    history: list[PromptMessage]


async def prepare_conversation_stream(
    session: AsyncSession,
    *,
    user_id: UUID,
    conversation_id: UUID,
    question: str | None,
    retry_of_message_id: UUID | None,
    model: str,
    pricing: ModelPricing,
    answer_input_tokens: int,
    answer_max_output_tokens: int,
) -> PreparedConversationStream:
    conversation = await get_owned_conversation(
        session,
        user_id=user_id,
        conversation_id=conversation_id,
        for_update=True,
    )
    messages = await get_conversation_messages(session, conversation_id=conversation.id)
    next_sequence = max((item.sequence_number for item in messages), default=0) + 1

    if question is not None:
        history = build_completed_history(messages)
        user_message = ConversationMessage(
            conversation_id=conversation.id,
            sequence_number=next_sequence,
            role="user",
            content=question,
            status="completed",
            completed_at=datetime.now(UTC),
        )
        session.add(user_message)
        assistant_sequence = next_sequence + 1
        retry_target_id = None
    else:
        retry_target = next(
            (
                item
                for item in messages
                if item.id == retry_of_message_id and item.role == "assistant"
            ),
            None,
        )
        if retry_target is None:
            raise _not_found()
        source_user = next(
            (
                item
                for item in reversed(messages)
                if item.role == "user" and item.sequence_number < retry_target.sequence_number
            ),
            None,
        )
        if source_user is None:
            raise _not_found()
        question = _validated_content(source_user)
        history = build_completed_history(
            item for item in messages if item.sequence_number < source_user.sequence_number
        )
        assistant_sequence = next_sequence
        retry_target_id = retry_target.id

    assistant = ConversationMessage(
        conversation_id=conversation.id,
        sequence_number=assistant_sequence,
        role="assistant",
        content="",
        status="streaming",
        retry_of_message_id=retry_target_id,
    )
    session.add(assistant)
    await session.flush()
    reserved_cost = calculate_reservation(
        pricing,
        input_tokens=answer_input_tokens,
        max_output_tokens=answer_max_output_tokens,
    )
    usage = create_usage_reservation(
        user_id=user_id,
        knowledge_base_id=conversation.knowledge_base_id,
        conversation_id=conversation.id,
        message_id=assistant.id,
        purpose="answer",
        model=model,
        pricing=pricing,
        reserved_cost=reserved_cost,
    )
    session.add(usage)
    await session.flush()
    return PreparedConversationStream(
        conversation_id=conversation.id,
        knowledge_base_id=conversation.knowledge_base_id,
        assistant_message_id=assistant.id,
        answer_usage_id=usage.id,
        question=question,
        history=history,
    )


@dataclass
class StreamPersistenceState:
    content: str = ""
    citations: list[dict[str, Any]] | None = None
    retrieval_stats: dict[str, Any] | None = None
    timings: dict[str, Any] | None = None
    usage: ChatUsage | None = None
    provider_request_id: str | None = None
    finish_reason: str | None = None
    answer_request_started: bool = False
    saw_done: bool = False
    was_rewritten: bool = False
    rewrite_fallback: bool = False
    refused: bool = False

    def observe(self, event) -> None:
        if event.event == "token":
            delta = event.data.get("delta")
            if isinstance(delta, str):
                self.content += delta
                if len(self.content) > 8000:
                    raise ValueError("助手回答超过 8000 字限制")
        persistence = event.persistence
        was_rewritten = persistence.get("was_rewritten")
        if isinstance(was_rewritten, bool):
            self.was_rewritten = was_rewritten
        rewrite_fallback = persistence.get("rewrite_fallback")
        if isinstance(rewrite_fallback, bool):
            self.rewrite_fallback = rewrite_fallback
        refused = persistence.get("refused")
        if isinstance(refused, bool):
            self.refused = refused
        self.answer_request_started = self.answer_request_started or bool(
            persistence.get("answer_request_started", False)
        )
        usage = persistence.get("usage")
        if isinstance(usage, ChatUsage):
            self.usage = usage
        provider_request_id = persistence.get("provider_request_id")
        if isinstance(provider_request_id, str):
            self.provider_request_id = provider_request_id
        finish_reason = persistence.get("finish_reason")
        if isinstance(finish_reason, str):
            self.finish_reason = finish_reason
        if event.event == "done":
            self.saw_done = True
            raw_citations = event.data.get("citations", [])
            self.citations = list(raw_citations) if isinstance(raw_citations, list) else []
            retrieved = event.data.get("retrieved_chunk_count", 0)
            self.retrieval_stats = {
                "retrieved_chunk_count": retrieved if isinstance(retrieved, int) else 0
            }
            raw_timings = event.data.get("timings", {})
            self.timings = dict(raw_timings) if isinstance(raw_timings, dict) else {}


async def finalize_conversation_stream(
    session: AsyncSession,
    *,
    prepared: PreparedConversationStream,
    state: StreamPersistenceState,
    outcome: Literal["completed", "client_disconnected", "canceled", "provider_failed"],
    pricing: ModelPricing,
    error_code: str | None,
) -> None:
    assistant = await session.get(ConversationMessage, prepared.assistant_message_id)
    usage_event = await session.get(LlmUsageEvent, prepared.answer_usage_id)
    if assistant is None or usage_event is None or assistant.status != "streaming":
        return

    completed_at = datetime.now(UTC)
    assistant.content = state.content
    assistant.completed_at = completed_at
    total_ms = None
    if state.timings is not None:
        raw_total = state.timings.get("total_ms")
        if isinstance(raw_total, int) and raw_total >= 0:
            total_ms = raw_total

    if outcome == "completed" and state.saw_done:
        assistant.status = "completed"
        assistant.citations_snapshot = state.citations or []
        assistant.retrieval_stats = state.retrieval_stats or {}
        assistant.timings = state.timings or {}
        assistant.finish_reason = state.finish_reason
        settlement = (
            settle_after_response(
                pricing=pricing,
                reserved_cost=usage_event.reserved_cost,
                usage=state.usage,
            )
            if state.answer_request_started
            else settle_after_failure(
                pricing=pricing,
                reserved_cost=usage_event.reserved_cost,
                request_started=False,
                usage=None,
            )
        )
        retrieved_count = int((state.retrieval_stats or {}).get("retrieved_chunk_count", 0))
        timings = state.timings or {}
        session.add(
            AnswerObservation(
                user_id=usage_event.user_id,
                knowledge_base_id=usage_event.knowledge_base_id,
                conversation_id=prepared.conversation_id,
                message_id=assistant.id,
                was_rewritten=state.was_rewritten,
                rewrite_fallback=state.rewrite_fallback,
                candidate_count=retrieved_count,
                accepted_count=retrieved_count,
                refused=state.refused,
                citation_count=len(state.citations or []),
                citations_valid=True,
                rewrite_ms=int(timings.get("rewrite_ms", 0)),
                retrieval_ms=int(timings.get("retrieval_ms", 0)),
                generation_ms=int(timings.get("generation_ms", 0)),
                total_ms=int(timings.get("total_ms", 0)),
                finish_reason=state.finish_reason,
            )
        )
    else:
        assistant.status = "failed" if outcome == "provider_failed" else "interrupted"
        assistant.error_code = error_code
        if outcome in {"client_disconnected", "canceled"} and state.answer_request_started:
            settlement = settle_after_response(
                pricing=pricing,
                reserved_cost=usage_event.reserved_cost,
                usage=state.usage,
            )
        else:
            settlement = settle_after_failure(
                pricing=pricing,
                reserved_cost=usage_event.reserved_cost,
                request_started=state.answer_request_started,
                usage=state.usage,
            )

    apply_settlement(
        usage_event,
        settlement=settlement,
        usage=state.usage,
        provider_request_id=state.provider_request_id,
        finish_reason=state.finish_reason,
        error_code=error_code,
        duration_ms=total_ms,
    )


def _validated_content(message: ConversationMessage) -> str:
    content = message.content.strip()
    limit = 2000 if message.role == "user" else 8000
    if not content or len(content) > limit:
        raise ValueError("历史消息内容长度不合法")
    return content


def build_completed_history(
    messages: Iterable[ConversationMessage],
) -> list[PromptMessage]:
    """只返回最后六组顺序相邻、均已完成的问答。"""
    ordered = sorted(messages, key=lambda item: item.sequence_number)
    pairs: list[tuple[ConversationMessage, ConversationMessage]] = []
    index = 0
    while index < len(ordered):
        user_message = ordered[index]
        if user_message.role != "user" or user_message.status != "completed":
            index += 1
            continue
        next_user = index + 1
        completed_assistants: list[ConversationMessage] = []
        while next_user < len(ordered) and ordered[next_user].role != "user":
            candidate = ordered[next_user]
            if candidate.role == "assistant" and candidate.status == "completed":
                completed_assistants.append(candidate)
            next_user += 1
        if completed_assistants:
            pairs.append((user_message, completed_assistants[-1]))
        index = next_user

    result: list[PromptMessage] = []
    for user_message, assistant_message in pairs[-6:]:
        result.extend(
            (
                PromptMessage(role="user", content=_validated_content(user_message)),
                PromptMessage(
                    role="assistant",
                    content=_validated_content(assistant_message),
                ),
            )
        )
    return result

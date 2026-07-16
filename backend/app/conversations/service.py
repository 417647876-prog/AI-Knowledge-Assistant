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
from app.observations.service import (
    ObservationMetrics,
    build_answer_observation,
)
from app.rag.prompt import estimate_rag_input_token_upper_bound
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
    answer_top_k: int,
    chunk_size: int,
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
        source_user = _assistant_source_user(retry_target, messages)
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
    strict_input_upper_bound = estimate_rag_input_token_upper_bound(
        question=question,
        history=history,
        top_k=answer_top_k,
        chunk_size=chunk_size,
    )
    reserved_cost = calculate_reservation(
        pricing,
        input_tokens=max(answer_input_tokens, strict_input_upper_bound),
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
    candidate_count: int = 0
    accepted_scores: tuple[float | None, ...] = ()
    rewrite_ms: int = 0
    retrieval_ms: int = 0
    generation_ms: int = 0
    total_ms: int = 0

    def observe(self, event) -> None:
        if event.event == "token":
            delta = event.data.get("delta")
            if isinstance(delta, str):
                remaining = 8000 - len(self.content)
                if len(delta) > remaining:
                    self.content += delta[:remaining]
                    raise AppError(
                        code="ANSWER_TOO_LONG",
                        message="助手回答超过长度限制。",
                        status_code=502,
                    )
                self.content += delta
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
        if event.event == "rewrite":
            self.rewrite_ms = _non_negative_int(event.data.get("elapsed_ms"))
        if event.event == "retrieval":
            self.retrieval_ms = _non_negative_int(event.data.get("elapsed_ms"))
            candidate_count = persistence.get("candidate_count")
            if isinstance(candidate_count, int) and candidate_count >= 0:
                self.candidate_count = candidate_count
            accepted_scores = persistence.get("accepted_scores")
            if isinstance(accepted_scores, (list, tuple)) and all(
                score is None or isinstance(score, (int, float)) for score in accepted_scores
            ):
                self.accepted_scores = tuple(
                    None if score is None else float(score) for score in accepted_scores
                )
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
            retrieved_count = self.retrieval_stats["retrieved_chunk_count"]
            if not self.accepted_scores and retrieved_count > 0:
                self.accepted_scores = (None,) * retrieved_count
            if self.candidate_count == 0:
                self.candidate_count = retrieved_count
            raw_timings = event.data.get("timings", {})
            self.timings = dict(raw_timings) if isinstance(raw_timings, dict) else {}
            self.rewrite_ms = _non_negative_int(self.timings.get("rewrite_ms"))
            self.retrieval_ms = _non_negative_int(self.timings.get("retrieval_ms"))
            self.generation_ms = _non_negative_int(self.timings.get("generation_ms"))
            self.total_ms = _non_negative_int(self.timings.get("total_ms"))

    def observation_metrics(self, *, error_code: str | None) -> ObservationMetrics:
        citation_ids = tuple(
            citation_id
            for item in self.citations or []
            if isinstance(item, dict) and isinstance((citation_id := item.get("citation_id")), int)
        )
        return ObservationMetrics(
            was_rewritten=self.was_rewritten,
            rewrite_fallback=self.rewrite_fallback,
            candidate_count=self.candidate_count,
            accepted_scores=self.accepted_scores,
            refused=self.refused,
            citation_ids=citation_ids,
            rewrite_ms=self.rewrite_ms,
            retrieval_ms=self.retrieval_ms,
            generation_ms=self.generation_ms,
            total_ms=self.total_ms,
            finish_reason=self.finish_reason,
            error_code=error_code,
        )


def _non_negative_int(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


async def finalize_conversation_stream(
    session: AsyncSession,
    *,
    prepared: PreparedConversationStream,
    state: StreamPersistenceState,
    outcome: Literal["completed", "client_disconnected", "canceled", "provider_failed"],
    pricing: ModelPricing,
    error_code: str | None,
    record_observation: bool = True,
) -> None:
    conversation = await session.scalar(
        select(Conversation).where(Conversation.id == prepared.conversation_id).with_for_update()
    )
    assistant = None
    if conversation is not None:
        assistant = await session.scalar(
            select(ConversationMessage)
            .where(ConversationMessage.id == prepared.assistant_message_id)
            .with_for_update()
        )
    usage_event = await session.scalar(
        select(LlmUsageEvent).where(LlmUsageEvent.id == prepared.answer_usage_id).with_for_update()
    )
    if usage_event is None or usage_event.status != "reserved":
        return

    completed_at = datetime.now(UTC)
    total_ms = None
    if state.timings is not None:
        raw_total = state.timings.get("total_ms")
        if isinstance(raw_total, int) and raw_total >= 0:
            total_ms = raw_total

    completed = outcome == "completed" and state.saw_done
    if completed:
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
    else:
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

    if assistant is not None and assistant.status == "streaming":
        assistant.content = state.content
        assistant.completed_at = completed_at
        if completed:
            assistant.status = "completed"
            assistant.citations_snapshot = state.citations or []
            assistant.retrieval_stats = state.retrieval_stats or {}
            assistant.timings = state.timings or {}
            assistant.finish_reason = state.finish_reason
        else:
            assistant.status = "failed" if outcome == "provider_failed" else "interrupted"
            assistant.error_code = error_code
        if record_observation:
            session.add(
                build_answer_observation(
                    user_id=usage_event.user_id,
                    knowledge_base_id=usage_event.knowledge_base_id,
                    conversation_id=prepared.conversation_id,
                    message_id=assistant.id,
                    metrics=state.observation_metrics(error_code=None if completed else error_code),
                )
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


def _assistant_source_user(
    assistant: ConversationMessage,
    messages: Iterable[ConversationMessage],
) -> ConversationMessage | None:
    ordered = sorted(messages, key=lambda item: item.sequence_number)
    by_id = {item.id: item for item in ordered}
    root = assistant
    visited: set[UUID] = set()
    while root.retry_of_message_id is not None:
        if root.id in visited:
            return None
        visited.add(root.id)
        target = by_id.get(root.retry_of_message_id)
        if target is None or target.role != "assistant":
            return None
        root = target
    return next(
        (
            item
            for item in reversed(ordered)
            if item.role == "user" and item.sequence_number < root.sequence_number
        ),
        None,
    )


def build_completed_history(
    messages: Iterable[ConversationMessage],
) -> list[PromptMessage]:
    """返回最后六组已完成的逻辑问答，并用最新完成重试替代原回答。"""
    ordered = sorted(messages, key=lambda item: item.sequence_number)
    completed_by_user: dict[UUID, ConversationMessage] = {}
    for assistant in ordered:
        if assistant.role != "assistant" or assistant.status != "completed":
            continue
        source_user = _assistant_source_user(assistant, ordered)
        if source_user is not None and source_user.status == "completed":
            completed_by_user[source_user.id] = assistant
    pairs = [
        (user_message, completed_by_user[user_message.id])
        for user_message in ordered
        if user_message.role == "user"
        and user_message.status == "completed"
        and user_message.id in completed_by_user
    ]

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

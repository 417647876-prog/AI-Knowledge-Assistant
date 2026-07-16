import asyncio
import os
from collections.abc import AsyncIterator
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from app.ai.contracts import ChatCompletion, ChatUsage
from app.ai.rewrite import ChatQuestionRewriter
from app.api.v1.conversations import (
    get_conversation_rag_service,
    stream_conversation_message,
)
from app.conversations.schemas import StreamConversationMessageRequest
from app.conversations.service import finalize_conversation_stream
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token, hash_password
from app.db.models import (
    USER_ROLE,
    AnswerObservation,
    Conversation,
    ConversationMessage,
    KnowledgeBase,
    LlmUsageEvent,
    User,
)
from app.db.session import session_factory
from app.main import create_app
from app.rag.streaming import StreamEvent, encode_sse
from app.usage.pricing import ModelPricing
from app.usage.service import ConversationUsageRecorder

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


class ControlledRagService:
    def __init__(self, events: list[StreamEvent], *, gate: bool = False) -> None:
        self.events = events
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if not gate:
            self.release.set()
        self.calls = 0

    async def stream_answer(self, knowledge_base_id, question, top_k, history, **kwargs):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        for event in self.events:
            yield event


def normal_events() -> list[StreamEvent]:
    usage = ChatUsage(
        cache_hit_input_tokens=10,
        cache_miss_input_tokens=20,
        output_tokens=30,
        reasoning_tokens=5,
        total_tokens=60,
        is_complete=True,
    )
    return [
        StreamEvent(
            "status",
            {"phase": "generating"},
            persistence={"answer_request_started": True},
        ),
        StreamEvent("token", {"delta": "答案。[1]"}),
        StreamEvent(
            "done",
            {
                "citations": [{"citation_id": 1, "file_name": "制度.pdf"}],
                "retrieved_chunk_count": 2,
                "timings": {
                    "rewrite_ms": 1,
                    "retrieval_ms": 2,
                    "generation_ms": 3,
                    "total_ms": 6,
                },
            },
            persistence={
                "usage": usage,
                "provider_request_id": f"provider-{uuid4()}",
                "finish_reason": "stop",
            },
        ),
    ]


@pytest.fixture
async def stream_resources() -> AsyncIterator[tuple[User, KnowledgeBase]]:
    user = User(
        username=f"stream_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    async with session_factory.begin() as session:
        session.add(user)
        await session.flush()
        knowledge_base = KnowledgeBase(name="流式持久化", owner_id=user.id)
        session.add(knowledge_base)
    try:
        yield user, knowledge_base
    finally:
        async with session_factory.begin() as session:
            conversation_ids = select(Conversation.id).where(Conversation.user_id == user.id)
            await session.execute(
                delete(AnswerObservation).where(
                    AnswerObservation.conversation_id.in_(conversation_ids)
                )
            )
            await session.execute(
                delete(ConversationMessage).where(
                    ConversationMessage.conversation_id.in_(conversation_ids)
                )
            )
            await session.execute(delete(Conversation).where(Conversation.id.in_(conversation_ids)))
            await session.execute(delete(LlmUsageEvent).where(LlmUsageEvent.user_id == user.id))
            await session.execute(
                delete(KnowledgeBase).where(KnowledgeBase.id == knowledge_base.id)
            )
            await session.execute(delete(User).where(User.id == user.id))


def test_persistence_metadata_is_not_serialized_into_sse_text() -> None:
    event = StreamEvent(
        "token",
        {"delta": "正文"},
        persistence={"usage": {"secret": "not-client-data"}},
    )

    assert b"not-client-data" not in encode_sse(event)


@pytest.mark.asyncio
async def test_stream_commits_placeholders_before_provider_and_finalizes_completed(
    stream_resources: tuple[User, KnowledgeBase],
) -> None:
    user, knowledge_base = stream_resources
    service = ControlledRagService(normal_events(), gate=True)
    app = create_app()
    app.dependency_overrides[get_conversation_rag_service] = lambda: service
    settings = Settings(
        _env_file=None,
        chat_cache_hit_input_price_per_million=Decimal("0.25"),
        chat_cache_miss_input_price_per_million=Decimal("1"),
        chat_output_price_per_million=Decimal("2"),
    )
    app.dependency_overrides[get_settings] = lambda: settings
    token = create_access_token(user_id=user.id, role=user.role, settings=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        created = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base.id}/conversations",
            json={"title": "持久化"},
        )
        conversation_id = created.json()["id"]
        response_task = asyncio.create_task(
            client.post(
                f"/api/v1/conversations/{conversation_id}/messages/stream",
                json={"question": "制度是什么？"},
            )
        )
        await asyncio.wait_for(service.entered.wait(), timeout=3)

        async with session_factory() as session:
            messages = list(
                (
                    await session.scalars(
                        select(ConversationMessage)
                        .where(ConversationMessage.conversation_id == UUID(conversation_id))
                        .order_by(ConversationMessage.sequence_number)
                    )
                ).all()
            )
            usages = list(
                (
                    await session.scalars(
                        select(LlmUsageEvent).where(
                            LlmUsageEvent.conversation_id == UUID(conversation_id)
                        )
                    )
                ).all()
            )
        assert [(item.role, item.status) for item in messages] == [
            ("user", "completed"),
            ("assistant", "streaming"),
        ]
        assert len(usages) == 1
        assert usages[0].status == "reserved"

        service.release.set()
        response = await asyncio.wait_for(response_task, timeout=5)
        assert response.status_code == 200
        assert "答案。[1]" in response.text

    async with session_factory() as session:
        assistant = await session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == UUID(conversation_id),
                ConversationMessage.role == "assistant",
            )
        )
        usage = await session.scalar(
            select(LlmUsageEvent).where(
                LlmUsageEvent.conversation_id == UUID(conversation_id),
                LlmUsageEvent.purpose == "answer",
            )
        )
        observation = await session.scalar(
            select(AnswerObservation).where(
                AnswerObservation.conversation_id == UUID(conversation_id)
            )
        )
    assert assistant is not None and assistant.status == "completed"
    assert assistant.content == "答案。[1]"
    assert assistant.citations_snapshot[0]["file_name"] == "制度.pdf"
    assert assistant.retrieval_stats == {"retrieved_chunk_count": 2}
    assert assistant.timings["total_ms"] == 6
    assert usage is not None and usage.status == "succeeded"
    assert usage.usage_complete is True
    assert observation is not None and observation.accepted_count == 2
    assert service.calls == 1


@pytest.mark.asyncio
async def test_provider_failure_marks_failed_without_automatic_retry(
    stream_resources: tuple[User, KnowledgeBase],
) -> None:
    user, knowledge_base = stream_resources

    class FailingService(ControlledRagService):
        async def stream_answer(self, *args, **kwargs):
            self.calls += 1
            yield StreamEvent(
                "status",
                {"phase": "generating"},
                persistence={"answer_request_started": True},
            )
            yield StreamEvent("token", {"delta": "部分"})
            raise RuntimeError("provider secret")

    service = FailingService([])
    app = create_app()
    app.dependency_overrides[get_conversation_rag_service] = lambda: service
    settings = Settings(_env_file=None)
    app.dependency_overrides[get_settings] = lambda: settings
    token = create_access_token(user_id=user.id, role=user.role, settings=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        created = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base.id}/conversations",
            json={"title": "失败"},
        )
        conversation_id = created.json()["id"]
        response = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json={"question": "问题"},
        )

    assert response.status_code == 200
    assert "provider secret" not in response.text
    async with session_factory() as session:
        assistant = await session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == UUID(conversation_id),
                ConversationMessage.role == "assistant",
            )
        )
        usage = await session.scalar(
            select(LlmUsageEvent).where(LlmUsageEvent.conversation_id == UUID(conversation_id))
        )
    assert assistant is not None and assistant.status == "failed"
    assert assistant.content == "部分"
    assert assistant.error_code == "CHAT_PROVIDER_ERROR"
    assert usage is not None and usage.status == "failed_after_request"
    assert service.calls == 1


@pytest.mark.asyncio
async def test_rewrite_and_answer_use_separate_reserved_and_settled_usage_events(
    stream_resources: tuple[User, KnowledgeBase],
) -> None:
    user, knowledge_base = stream_resources
    rewrite_usage = ChatUsage(2, 3, 4, 0, 9, True)

    class RewritingService(ControlledRagService):
        async def stream_answer(self, *args, usage_recorder, **kwargs):
            await usage_recorder.before_rewrite_request()
            await usage_recorder.rewrite_completed(
                ChatCompletion(
                    "独立问题",
                    rewrite_usage,
                    "stop",
                    f"rewrite-{uuid4()}",
                )
            )
            for event in self.events:
                yield event

    service = RewritingService(normal_events())
    settings = Settings(
        _env_file=None,
        chat_cache_hit_input_price_per_million=Decimal("0.25"),
        chat_cache_miss_input_price_per_million=Decimal("1"),
        chat_output_price_per_million=Decimal("2"),
    )
    app = create_app()
    app.dependency_overrides[get_conversation_rag_service] = lambda: service
    app.dependency_overrides[get_settings] = lambda: settings
    token = create_access_token(user_id=user.id, role=user.role, settings=settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        created = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base.id}/conversations",
            json={"title": "改写用量"},
        )
        conversation_id = UUID(created.json()["id"])
        response = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json={"question": "它呢？"},
        )

    assert response.status_code == 200
    async with session_factory() as session:
        usages = list(
            (
                await session.scalars(
                    select(LlmUsageEvent)
                    .where(LlmUsageEvent.conversation_id == conversation_id)
                    .order_by(LlmUsageEvent.created_at)
                )
            ).all()
        )
    assert [item.purpose for item in usages] == ["answer", "rewrite"]
    assert [item.status for item in usages] == ["succeeded", "succeeded"]
    assert usages[1].total_tokens == 9
    assert usages[1].settled_cost < usages[1].reserved_cost


@pytest.mark.asyncio
async def test_rewrite_callback_failures_never_leave_reserved_usage(
    stream_resources: tuple[User, KnowledgeBase],
) -> None:
    user, knowledge_base = stream_resources
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            title="回调故障",
        )
        session.add(conversation)
        await session.flush()
        before_message = ConversationMessage(
            conversation_id=conversation.id,
            sequence_number=1,
            role="assistant",
            content="",
            status="streaming",
        )
        after_message = ConversationMessage(
            conversation_id=conversation.id,
            sequence_number=2,
            role="assistant",
            content="",
            status="streaming",
        )
        session.add_all((before_message, after_message))
        await session.flush()

    pricing = ModelPricing(Decimal("0.25"), Decimal("1"), Decimal("2"))

    class FailsAfterCommittedReservation(ConversationUsageRecorder):
        async def before_rewrite_request(self) -> None:
            await super().before_rewrite_request()
            raise RuntimeError("reserve observer failed")

    class FailsInsideSettlementTransaction(ConversationUsageRecorder):
        async def rewrite_completed(self, completion: ChatCompletion) -> None:
            async with self._session_factory.begin() as session:
                event = await session.get(
                    LlmUsageEvent,
                    self._rewrite_usage_id,
                    with_for_update=True,
                )
                assert event is not None
                event.error_code = "ROLLBACK_ME"
                await session.flush()
                raise RuntimeError("settlement observer failed")

    class CompletionProvider:
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, system_prompt, user_prompt, *, max_output_tokens=None):
            self.calls += 1
            return ChatCompletion(
                "独立问题",
                ChatUsage(1, 2, 3, 0, 6, True),
                "stop",
                f"rewrite-failure-{uuid4()}",
            )

    def recorder(recorder_type, message_id):
        return recorder_type(
            session_factory=session_factory,
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            conversation_id=conversation.id,
            message_id=message_id,
            model="fake",
            pricing=pricing,
            rewrite_input_tokens=100,
            rewrite_max_output_tokens=20,
        )

    before_provider = CompletionProvider()
    before_recorder = recorder(FailsAfterCommittedReservation, before_message.id)
    with pytest.raises(AppError):
        await ChatQuestionRewriter(before_provider).rewrite_tracked(
            [],
            "追问",
            max_output_tokens=20,
            before_request=before_recorder.before_rewrite_request,
            on_completion=before_recorder.rewrite_completed,
            on_failure=before_recorder.rewrite_failed,
        )
    assert before_provider.calls == 0

    after_provider = CompletionProvider()
    after_recorder = recorder(FailsInsideSettlementTransaction, after_message.id)
    with pytest.raises(AppError):
        await ChatQuestionRewriter(after_provider).rewrite_tracked(
            [],
            "追问",
            max_output_tokens=20,
            before_request=after_recorder.before_rewrite_request,
            on_completion=after_recorder.rewrite_completed,
            on_failure=after_recorder.rewrite_failed,
        )
    assert after_provider.calls == 1

    async with session_factory() as session:
        usages = list(
            (
                await session.scalars(
                    select(LlmUsageEvent)
                    .where(LlmUsageEvent.conversation_id == conversation.id)
                    .order_by(LlmUsageEvent.message_id)
                )
            ).all()
        )
    by_message = {item.message_id: item for item in usages}
    before = by_message[before_message.id]
    after = by_message[after_message.id]
    assert before.status == "failed_before_request"
    assert before.settled_cost == Decimal("0.000000")
    assert after.status == "failed_after_request"
    assert after.settled_cost < after.reserved_cost
    assert after.total_tokens == 6
    assert after.usage_complete is True
    assert after.error_code == "QUESTION_REWRITE_ERROR"


@pytest.mark.asyncio
async def test_real_database_disconnect_marks_interrupted_and_keeps_partial_body(
    stream_resources: tuple[User, KnowledgeBase],
) -> None:
    user, knowledge_base = stream_resources
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            title="断线",
        )
        session.add(conversation)
        await session.flush()

    class DisconnectAfterFirstEvent:
        def __init__(self) -> None:
            self.state = SimpleNamespace(request_id="disconnect-pg")
            self.polls = 0

        async def is_disconnected(self) -> bool:
            self.polls += 1
            return self.polls > 2

    class PartialService:
        async def stream_answer(self, *args, **kwargs):
            yield StreamEvent(
                "status",
                {"phase": "generating"},
                persistence={"answer_request_started": True},
            )
            yield StreamEvent("token", {"delta": "服务端已收到的部分"})
            await asyncio.Event().wait()

    settings = Settings(_env_file=None)
    async with session_factory() as endpoint_session:
        response = await stream_conversation_message(
            conversation_id=conversation.id,
            payload=StreamConversationMessageRequest(question="问题"),
            request=DisconnectAfterFirstEvent(),  # type: ignore[arg-type]
            session=endpoint_session,
            current_user=user,
            service=PartialService(),  # type: ignore[arg-type]
            settings=settings,
        )
        body = b"".join([part async for part in response.body_iterator])

    assert "服务端已收到的部分".encode() in body
    async with session_factory() as session:
        assistant = await session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.role == "assistant",
            )
        )
        usage = await session.scalar(
            select(LlmUsageEvent).where(
                LlmUsageEvent.conversation_id == conversation.id,
                LlmUsageEvent.purpose == "answer",
            )
        )
    assert assistant is not None and assistant.status == "interrupted"
    assert assistant.content == "服务端已收到的部分"
    assert assistant.error_code == "CLIENT_DISCONNECTED"
    assert usage is not None and usage.status == "usage_unknown"
    assert usage.settled_cost == usage.reserved_cost


@pytest.mark.asyncio
async def test_real_done_without_usage_completes_message_but_keeps_reserved_cost_unknown(
    stream_resources: tuple[User, KnowledgeBase],
) -> None:
    user, knowledge_base = stream_resources
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            title="缺失usage",
        )
        session.add(conversation)
        await session.flush()

    events = normal_events()
    events[-1] = StreamEvent(
        "done",
        events[-1].data,
        persistence={
            "provider_request_id": f"unknown-{uuid4()}",
            "finish_reason": "stop",
        },
    )

    class ConnectedRequest:
        state = SimpleNamespace(request_id="usage-unknown-pg")

        async def is_disconnected(self) -> bool:
            return False

    settings = Settings(
        _env_file=None,
        chat_cache_hit_input_price_per_million=Decimal("0.25"),
        chat_cache_miss_input_price_per_million=Decimal("1"),
        chat_output_price_per_million=Decimal("2"),
    )
    async with session_factory() as endpoint_session:
        response = await stream_conversation_message(
            conversation_id=conversation.id,
            payload=StreamConversationMessageRequest(question="问题"),
            request=ConnectedRequest(),  # type: ignore[arg-type]
            session=endpoint_session,
            current_user=user,
            service=ControlledRagService(events),  # type: ignore[arg-type]
            settings=settings,
        )
        _ = b"".join([part async for part in response.body_iterator])

    async with session_factory() as session:
        assistant = await session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.role == "assistant",
            )
        )
        usage = await session.scalar(
            select(LlmUsageEvent).where(
                LlmUsageEvent.conversation_id == conversation.id,
                LlmUsageEvent.purpose == "answer",
            )
        )
    assert assistant is not None and assistant.status == "completed"
    assert usage is not None and usage.status == "usage_unknown"
    assert usage.usage_complete is False
    assert usage.settled_cost == usage.reserved_cost
    assert usage.reserved_cost > 0


@pytest.mark.asyncio
async def test_observation_persistence_failure_can_never_mark_answer_completed(
    stream_resources: tuple[User, KnowledgeBase],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user, knowledge_base = stream_resources
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            title="观测失败",
        )
        session.add(conversation)
        await session.flush()

    async def fail_completed_observation(session, **kwargs):
        if kwargs["outcome"] == "completed":
            raise RuntimeError("observation storage failed")
        return await finalize_conversation_stream(session, **kwargs)

    monkeypatch.setattr(
        "app.api.v1.conversations.finalize_conversation_stream",
        fail_completed_observation,
    )

    class ConnectedRequest:
        state = SimpleNamespace(request_id="observation-failed-pg")

        async def is_disconnected(self) -> bool:
            return False

    settings = Settings(_env_file=None)
    async with session_factory() as endpoint_session:
        response = await stream_conversation_message(
            conversation_id=conversation.id,
            payload=StreamConversationMessageRequest(question="问题"),
            request=ConnectedRequest(),  # type: ignore[arg-type]
            session=endpoint_session,
            current_user=user,
            service=ControlledRagService(normal_events()),  # type: ignore[arg-type]
            settings=settings,
        )
        body = b"".join([part async for part in response.body_iterator])

    assert b"event: error" in body
    async with session_factory() as session:
        assistant = await session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.role == "assistant",
            )
        )
        observation = await session.scalar(
            select(AnswerObservation).where(AnswerObservation.conversation_id == conversation.id)
        )
    assert assistant is not None and assistant.status == "failed"
    assert assistant.error_code == "PERSISTENCE_ERROR"
    assert observation is None


@pytest.mark.asyncio
async def test_real_database_cancellation_marks_interrupted_and_closes_source(
    stream_resources: tuple[User, KnowledgeBase],
) -> None:
    user, knowledge_base = stream_resources
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            title="取消",
        )
        session.add(conversation)
        await session.flush()

    class ConnectedRequest:
        state = SimpleNamespace(request_id="cancel-pg")

        async def is_disconnected(self) -> bool:
            return False

    class CancellableService:
        def __init__(self) -> None:
            self.closed = False

        async def stream_answer(self, *args, **kwargs):
            try:
                yield StreamEvent(
                    "status",
                    {"phase": "generating"},
                    persistence={"answer_request_started": True},
                )
                yield StreamEvent("token", {"delta": "取消前部分"})
                await asyncio.Event().wait()
            finally:
                self.closed = True

    service = CancellableService()
    settings = Settings(_env_file=None)
    async with session_factory() as endpoint_session:
        response = await stream_conversation_message(
            conversation_id=conversation.id,
            payload=StreamConversationMessageRequest(question="问题"),
            request=ConnectedRequest(),  # type: ignore[arg-type]
            session=endpoint_session,
            current_user=user,
            service=service,  # type: ignore[arg-type]
            settings=settings,
        )
        iterator = response.body_iterator
        assert b"event: status" in await anext(iterator)
        assert "取消前部分".encode() in await anext(iterator)
        pending = asyncio.create_task(anext(iterator))
        await asyncio.sleep(0)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending

    async with session_factory() as session:
        assistant = await session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.role == "assistant",
            )
        )
        usage = await session.scalar(
            select(LlmUsageEvent).where(
                LlmUsageEvent.conversation_id == conversation.id,
                LlmUsageEvent.purpose == "answer",
            )
        )
    assert service.closed is True
    assert assistant is not None and assistant.status == "interrupted"
    assert assistant.content == "取消前部分"
    assert assistant.error_code == "STREAM_CANCELED"
    assert usage is not None and usage.status == "usage_unknown"

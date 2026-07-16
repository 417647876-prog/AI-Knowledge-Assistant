import asyncio
import os
from collections.abc import AsyncIterator
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, select, text
from starlette.requests import ClientDisconnect

from app.ai.contracts import ChatCompletion, ChatUsage
from app.ai.rewrite import ChatQuestionRewriter
from app.api.v1.conversations import (
    get_conversation_rag_service,
    stream_conversation_message,
)
from app.conversations.schemas import StreamConversationMessageRequest
from app.conversations.service import (
    StreamPersistenceState,
    delete_conversation_body,
    finalize_conversation_stream,
    prepare_conversation_stream,
)
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
        async def before_rewrite_request(self, input_token_upper_bound: int) -> None:
            await super().before_rewrite_request(input_token_upper_bound)
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


@pytest.mark.asyncio
async def test_asgi_send_oserror_waits_for_interrupted_persistence_and_source_close(
    stream_resources: tuple[User, KnowledgeBase],
) -> None:
    user, knowledge_base = stream_resources
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            title="ASGI 断线",
        )
        session.add(conversation)
        await session.flush()

    class ConnectedRequest:
        state = SimpleNamespace(request_id="asgi-send-oserror")

        async def is_disconnected(self) -> bool:
            return False

    class SendFailureService:
        def __init__(self) -> None:
            self.closed = False

        async def stream_answer(self, *args, **kwargs):
            try:
                yield StreamEvent(
                    "status",
                    {"phase": "generating"},
                    persistence={"answer_request_started": True},
                )
                yield StreamEvent("token", {"delta": "断线前部分"})
                await asyncio.Event().wait()
            finally:
                self.closed = True

    service = SendFailureService()
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

    async def receive():
        await asyncio.Event().wait()

    async def send(message) -> None:
        if message["type"] == "http.response.body" and "断线前部分".encode() in message["body"]:
            raise OSError("ASGI 2.4 disconnected")

    with pytest.raises(ClientDisconnect):
        await response(
            {"type": "http", "asgi": {"spec_version": "2.4"}},
            receive,
            send,
        )

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
    assert assistant.content == "断线前部分"
    assert usage is not None and usage.status != "reserved"
    assert service.closed is True
    assert not [
        task
        for task in asyncio.all_tasks()
        if not task.done() and "iter_sse.<locals>.produce" in repr(task.get_coro())
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["provider_failed", "client_disconnected", "canceled"])
async def test_first_finalization_transaction_failure_recovers_every_outcome(
    stream_resources: tuple[User, KnowledgeBase],
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
) -> None:
    user, knowledge_base = stream_resources
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            title=f"恢复-{outcome}",
        )
        session.add(conversation)
        await session.flush()

    original_finalize = finalize_conversation_stream
    calls: list[tuple[str, str | None]] = []

    async def fail_original_outcome(session, **kwargs):
        calls.append((kwargs["outcome"], kwargs["error_code"]))
        if kwargs["error_code"] != "PERSISTENCE_ERROR":
            raise RuntimeError("first finalization transaction failed")
        return await original_finalize(session, **kwargs)

    monkeypatch.setattr(
        "app.api.v1.conversations.finalize_conversation_stream",
        fail_original_outcome,
    )

    class ConnectedRequest:
        state = SimpleNamespace(request_id=f"recover-{outcome}")

        async def is_disconnected(self) -> bool:
            return False

    class OutcomeService:
        async def stream_answer(self, *args, **kwargs):
            yield StreamEvent(
                "status",
                {"phase": "generating"},
                persistence={"answer_request_started": True},
            )
            yield StreamEvent("token", {"delta": "部分"})
            if outcome == "provider_failed":
                raise AppError(code="CHAT_PROVIDER_ERROR", message="失败", status_code=502)
            await asyncio.Event().wait()

    settings = Settings(_env_file=None)
    async with session_factory() as endpoint_session:
        response = await stream_conversation_message(
            conversation_id=conversation.id,
            payload=StreamConversationMessageRequest(question="问题"),
            request=ConnectedRequest(),  # type: ignore[arg-type]
            session=endpoint_session,
            current_user=user,
            service=OutcomeService(),  # type: ignore[arg-type]
            settings=settings,
        )
        iterator = response.body_iterator
        if outcome == "provider_failed":
            with pytest.raises(RuntimeError):
                _ = [part async for part in iterator]
        else:
            assert b"event: status" in await anext(iterator)
            assert "部分".encode() in await anext(iterator)
            if outcome == "client_disconnected":
                with pytest.raises(RuntimeError):
                    await iterator.aclose()
            else:
                pending = asyncio.create_task(anext(iterator))
                await asyncio.sleep(0)
                pending.cancel()
                with pytest.raises((asyncio.CancelledError, RuntimeError)):
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
    recovery_outcome = "provider_failed" if outcome == "provider_failed" else outcome
    assert (recovery_outcome, "PERSISTENCE_ERROR") in calls
    expected_message_status = "failed" if outcome == "provider_failed" else "interrupted"
    assert assistant is not None and assistant.status == expected_message_status
    assert assistant.error_code == "PERSISTENCE_ERROR"
    expected_usage_status = (
        "failed_after_request" if outcome == "provider_failed" else "usage_unknown"
    )
    assert usage is not None and usage.status == expected_usage_status


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_fails", [False, True], ids=["completed", "failed"])
async def test_stream_finalization_settles_usage_after_conversation_is_deleted(
    stream_resources: tuple[User, KnowledgeBase],
    provider_fails: bool,
) -> None:
    user, knowledge_base = stream_resources
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            title="流中删除",
        )
        session.add(conversation)
        await session.flush()

    class ConnectedRequest:
        state = SimpleNamespace(request_id="delete-during-stream")

        async def is_disconnected(self) -> bool:
            return False

    class DeleteRaceService:
        async def stream_answer(self, *args, **kwargs):
            yield StreamEvent(
                "status",
                {"phase": "generating"},
                persistence={"answer_request_started": True},
            )
            yield StreamEvent("token", {"delta": "将被删除"})
            if provider_fails:
                raise AppError(code="CHAT_PROVIDER_ERROR", message="失败", status_code=502)
            for event in normal_events()[2:]:
                yield event

    settings = Settings(_env_file=None)
    async with session_factory() as endpoint_session:
        response = await stream_conversation_message(
            conversation_id=conversation.id,
            payload=StreamConversationMessageRequest(question="问题"),
            request=ConnectedRequest(),  # type: ignore[arg-type]
            session=endpoint_session,
            current_user=user,
            service=DeleteRaceService(),  # type: ignore[arg-type]
            settings=settings,
        )

    async with session_factory.begin() as delete_session:
        await delete_conversation_body(
            delete_session,
            user_id=user.id,
            conversation_id=conversation.id,
        )

    _ = [part async for part in response.body_iterator]

    async with session_factory() as session:
        conversation_row = await session.get(Conversation, conversation.id)
        message_row = await session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id
            )
        )
        usage = await session.scalar(
            select(LlmUsageEvent).where(
                LlmUsageEvent.conversation_id == conversation.id,
                LlmUsageEvent.purpose == "answer",
            )
        )
    assert conversation_row is None
    assert message_row is None
    assert usage is not None and usage.status != "reserved"


@pytest.mark.asyncio
async def test_answer_usage_above_old_fixed_input_is_covered_by_reservation(
    stream_resources: tuple[User, KnowledgeBase],
) -> None:
    user, knowledge_base = stream_resources
    usage = ChatUsage(0, 40_000, 0, 0, 40_000, True)
    events = normal_events()
    events[-1] = StreamEvent(
        "done",
        events[-1].data,
        persistence={"usage": usage, "finish_reason": "stop", "provider_request_id": str(uuid4())},
    )

    class HighUsageService(ControlledRagService):
        async def stream_answer(self, *args, usage_recorder, **kwargs):
            await usage_recorder.before_answer_request(40_000)
            async for event in super().stream_answer(
                *args,
                usage_recorder=usage_recorder,
                **kwargs,
            ):
                yield event

    service = HighUsageService(events)
    settings = Settings(
        _env_file=None,
        chat_cache_hit_input_price_per_million=Decimal("1"),
        chat_cache_miss_input_price_per_million=Decimal("1"),
        chat_output_price_per_million=Decimal("1"),
        chat_answer_input_token_reserve=1,
        chunk_size=1,
        chunk_overlap=0,
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
            json={"title": "严格预留"},
        )
        conversation_id = UUID(created.json()["id"])
        response = await client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json={"question": "问题", "top_k": 1},
        )

    assert response.status_code == 200
    async with session_factory() as session:
        answer_usage = await session.scalar(
            select(LlmUsageEvent).where(
                LlmUsageEvent.conversation_id == conversation_id,
                LlmUsageEvent.purpose == "answer",
            )
        )
    assert answer_usage is not None
    assert answer_usage.settled_cost is not None
    assert answer_usage.settled_cost <= answer_usage.reserved_cost


@pytest.mark.asyncio
async def test_answer_too_long_persists_only_first_8000_characters_and_fails(
    stream_resources: tuple[User, KnowledgeBase],
) -> None:
    user, knowledge_base = stream_resources
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            title="回答超长",
        )
        session.add(conversation)
        await session.flush()

    class ConnectedRequest:
        state = SimpleNamespace(request_id="answer-too-long")

        async def is_disconnected(self) -> bool:
            return False

    events = [
        StreamEvent(
            "status",
            {"phase": "generating"},
            persistence={"answer_request_started": True},
        ),
        StreamEvent("token", {"delta": "答" * 8000}),
        StreamEvent("token", {"delta": "超"}),
    ]
    async with session_factory() as endpoint_session:
        response = await stream_conversation_message(
            conversation_id=conversation.id,
            payload=StreamConversationMessageRequest(question="问题"),
            request=ConnectedRequest(),  # type: ignore[arg-type]
            session=endpoint_session,
            current_user=user,
            service=ControlledRagService(events),  # type: ignore[arg-type]
            settings=Settings(_env_file=None),
        )
        body = b"".join([part async for part in response.body_iterator])

    assert b'"code":"ANSWER_TOO_LONG"' in body
    async with session_factory() as session:
        assistant = await session.scalar(
            select(ConversationMessage).where(
                ConversationMessage.conversation_id == conversation.id,
                ConversationMessage.role == "assistant",
            )
        )
    assert assistant is not None and assistant.status == "failed"
    assert assistant.error_code == "ANSWER_TOO_LONG"
    assert len(assistant.content) == 8000


@pytest.mark.asyncio
async def test_delete_and_completed_finalization_use_one_lock_order_without_deadlock(
    stream_resources: tuple[User, KnowledgeBase],
) -> None:
    user, knowledge_base = stream_resources
    pricing = ModelPricing(Decimal("0.25"), Decimal("1"), Decimal("2"))
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=user.id,
            knowledge_base_id=knowledge_base.id,
            title="删除与完成并发",
        )
        session.add(conversation)
        await session.flush()
        prepared = await prepare_conversation_stream(
            session,
            user_id=user.id,
            conversation_id=conversation.id,
            question="并发问题",
            retry_of_message_id=None,
            model="fake",
            pricing=pricing,
            answer_input_tokens=100,
            answer_max_output_tokens=20,
            answer_top_k=1,
            chunk_size=100,
        )

    state = StreamPersistenceState()
    for event in normal_events():
        state.observe(event)

    delete_ready = asyncio.Event()
    begin_delete = asyncio.Event()
    finalizer_first_lock = asyncio.Event()
    continue_finalizer = asyncio.Event()
    delete_pid: list[int] = []

    class FirstScalarGate:
        def __init__(self, session) -> None:
            self._session = session
            self._paused = False

        def __getattr__(self, name):
            return getattr(self._session, name)

        async def scalar(self, statement, *args, **kwargs):
            result = await self._session.scalar(statement, *args, **kwargs)
            if not self._paused:
                self._paused = True
                finalizer_first_lock.set()
                await continue_finalizer.wait()
            return result

    async def run_delete() -> None:
        async with session_factory.begin() as session:
            await session.execute(text("SET LOCAL lock_timeout = '2s'"))
            delete_pid.append(int(await session.scalar(text("SELECT pg_backend_pid()"))))
            delete_ready.set()
            await begin_delete.wait()
            await delete_conversation_body(
                session,
                user_id=user.id,
                conversation_id=conversation.id,
            )

    async def run_finalizer() -> None:
        async with session_factory.begin() as session:
            await session.execute(text("SET LOCAL lock_timeout = '2s'"))
            await finalize_conversation_stream(
                FirstScalarGate(session),  # type: ignore[arg-type]
                prepared=prepared,
                state=state,
                outcome="completed",
                pricing=pricing,
                error_code=None,
            )

    delete_task = asyncio.create_task(run_delete())
    await asyncio.wait_for(delete_ready.wait(), timeout=2)
    finalizer_task = asyncio.create_task(run_finalizer())
    await asyncio.wait_for(finalizer_first_lock.wait(), timeout=2)
    begin_delete.set()

    for _ in range(100):
        async with session_factory() as observer:
            wait_event_type = await observer.scalar(
                text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                {"pid": delete_pid[0]},
            )
        if wait_event_type == "Lock":
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("DELETE 未按事件栅栏进入数据库锁等待")

    continue_finalizer.set()
    results = await asyncio.wait_for(
        asyncio.gather(finalizer_task, delete_task, return_exceptions=True),
        timeout=5,
    )
    assert results == [None, None]

    async with session_factory() as session:
        usage = await session.get(LlmUsageEvent, prepared.answer_usage_id)
    assert usage is not None and usage.status != "reserved"

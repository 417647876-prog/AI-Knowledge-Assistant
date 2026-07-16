import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select, text

from app.api.v1.conversations import get_conversation_rag_service
from app.conversations.service import prepare_conversation_stream
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db.models import (
    USER_ROLE,
    AnswerFeedback,
    AnswerObservation,
    Conversation,
    ConversationMessage,
    KnowledgeBase,
    LlmUsageEvent,
    RefreshSession,
    User,
)
from app.db.session import session_factory
from app.main import create_app
from app.rag.streaming import StreamEvent
from app.usage.pricing import ModelPricing

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@dataclass
class ConversationContext:
    client: httpx.AsyncClient
    other_client: httpx.AsyncClient
    user_id: UUID
    other_user_id: UUID
    knowledge_base_id: UUID
    deleted_knowledge_base_id: UUID
    other_knowledge_base_id: UUID
    app: object


@pytest.fixture
async def conversation_context() -> AsyncIterator[ConversationContext]:
    user = User(
        id=uuid4(),
        username=f"conv_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    other_user = User(
        id=uuid4(),
        username=f"conv_other_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    async with session_factory.begin() as session:
        session.add_all((user, other_user))
        await session.flush()
        knowledge_base = KnowledgeBase(name="当前知识库", owner_id=user.id)
        deleted_knowledge_base = KnowledgeBase(
            name="已删除知识库",
            owner_id=user.id,
            deleted_at=func.now(),
        )
        other_knowledge_base = KnowledgeBase(name="他人知识库", owner_id=other_user.id)
        session.add_all((knowledge_base, deleted_knowledge_base, other_knowledge_base))

    settings = get_settings()
    token = create_access_token(user_id=user.id, role=user.role, settings=settings)
    other_token = create_access_token(
        user_id=other_user.id,
        role=other_user.role,
        settings=settings,
    )
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client,
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {other_token}"},
        ) as other_client,
    ):
        try:
            yield ConversationContext(
                client=client,
                other_client=other_client,
                user_id=user.id,
                other_user_id=other_user.id,
                knowledge_base_id=knowledge_base.id,
                deleted_knowledge_base_id=deleted_knowledge_base.id,
                other_knowledge_base_id=other_knowledge_base.id,
                app=app,
            )
        finally:
            async with session_factory.begin() as session:
                conversation_ids = select(Conversation.id).where(
                    Conversation.user_id.in_((user.id, other_user.id))
                )
                message_ids = select(ConversationMessage.id).where(
                    ConversationMessage.conversation_id.in_(conversation_ids)
                )
                await session.execute(
                    delete(AnswerFeedback).where(AnswerFeedback.message_id.in_(message_ids))
                )
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
                await session.execute(
                    delete(Conversation).where(Conversation.id.in_(conversation_ids))
                )
                await session.execute(
                    delete(LlmUsageEvent).where(LlmUsageEvent.user_id.in_((user.id, other_user.id)))
                )
                await session.execute(
                    delete(KnowledgeBase).where(
                        KnowledgeBase.owner_id.in_((user.id, other_user.id))
                    )
                )
                await session.execute(
                    delete(RefreshSession).where(
                        RefreshSession.user_id.in_((user.id, other_user.id))
                    )
                )
                await session.execute(delete(User).where(User.id.in_((user.id, other_user.id))))


@pytest.mark.asyncio
async def test_conversation_crud_is_owner_scoped_and_paginated(
    conversation_context: ConversationContext,
) -> None:
    created_ids = []
    for title in ("第一段会话", "第二段会话", "第三段会话"):
        response = await conversation_context.client.post(
            f"/api/v1/knowledge-bases/{conversation_context.knowledge_base_id}/conversations",
            json={"title": title},
        )
        assert response.status_code == 201
        created_ids.append(response.json()["id"])

    page = await conversation_context.client.get(
        f"/api/v1/knowledge-bases/{conversation_context.knowledge_base_id}/conversations",
        params={"page": 2, "page_size": 2},
    )
    assert page.status_code == 200
    assert page.json()["page"] == 2
    assert page.json()["page_size"] == 2
    assert page.json()["total"] == 3
    assert len(page.json()["items"]) == 1

    hidden = await conversation_context.other_client.get(f"/api/v1/conversations/{created_ids[0]}")
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_create_requires_current_users_active_knowledge_base(
    conversation_context: ConversationContext,
) -> None:
    for knowledge_base_id in (
        conversation_context.deleted_knowledge_base_id,
        conversation_context.other_knowledge_base_id,
        uuid4(),
    ):
        response = await conversation_context.client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/conversations",
            json={"title": "不应创建"},
        )
        assert response.status_code == 404


@pytest.mark.asyncio
async def test_detail_returns_messages_and_citation_snapshots(
    conversation_context: ConversationContext,
) -> None:
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=conversation_context.user_id,
            knowledge_base_id=conversation_context.knowledge_base_id,
            title="详情",
        )
        session.add(conversation)
        await session.flush()
        session.add_all(
            (
                ConversationMessage(
                    conversation_id=conversation.id,
                    sequence_number=1,
                    role="user",
                    content="问题",
                    status="completed",
                    completed_at=func.now(),
                ),
                ConversationMessage(
                    conversation_id=conversation.id,
                    sequence_number=2,
                    role="assistant",
                    content="答案。[1]",
                    status="completed",
                    citations_snapshot=[{"citation_id": 1, "file_name": "制度.pdf"}],
                    retrieval_stats={"retrieved_chunk_count": 1},
                    timings={"total_ms": 42},
                    finish_reason="stop",
                    completed_at=func.now(),
                ),
            )
        )

    response = await conversation_context.client.get(f"/api/v1/conversations/{conversation.id}")

    assert response.status_code == 200
    assert [item["role"] for item in response.json()["messages"]] == ["user", "assistant"]
    assert response.json()["messages"][1]["citations_snapshot"] == [
        {"citation_id": 1, "file_name": "制度.pdf"}
    ]


@pytest.mark.asyncio
async def test_delete_removes_body_but_preserves_task7_usage_fact(
    conversation_context: ConversationContext,
) -> None:
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=conversation_context.user_id,
            knowledge_base_id=conversation_context.knowledge_base_id,
            title="可删除",
        )
        session.add(conversation)
        await session.flush()
        assistant = ConversationMessage(
            conversation_id=conversation.id,
            sequence_number=1,
            role="assistant",
            content="回答",
            status="completed",
            completed_at=func.now(),
        )
        session.add(assistant)
        await session.flush()
        usage = LlmUsageEvent(
            user_id=conversation_context.user_id,
            knowledge_base_id=conversation_context.knowledge_base_id,
            conversation_id=conversation.id,
            message_id=assistant.id,
            purpose="answer",
            status="succeeded",
            model="fake",
            cache_hit_input_tokens=0,
            cache_miss_input_tokens=1,
            output_tokens=1,
            reasoning_tokens=0,
            total_tokens=2,
            usage_complete=True,
            price_snapshot={"unit": "per_million_tokens"},
            reserved_cost=Decimal("0.100000"),
            settled_cost=Decimal("0.050000"),
            completed_at=func.now(),
        )
        session.add(usage)

    response = await conversation_context.client.delete(f"/api/v1/conversations/{conversation.id}")

    assert response.status_code == 204
    async with session_factory() as session:
        assert await session.get(Conversation, conversation.id) is None
        preserved = await session.get(LlmUsageEvent, usage.id)
        assert preserved is not None
        assert preserved.conversation_id == conversation.id
        assert preserved.message_id == assistant.id


@pytest.mark.asyncio
async def test_stream_request_requires_exactly_one_of_question_or_retry(
    conversation_context: ConversationContext,
) -> None:
    created = await conversation_context.client.post(
        f"/api/v1/knowledge-bases/{conversation_context.knowledge_base_id}/conversations",
        json={"title": "流式"},
    )
    conversation_id = created.json()["id"]

    for payload in ({}, {"question": "问题", "retry_of_message_id": str(uuid4())}):
        response = await conversation_context.client.post(
            f"/api/v1/conversations/{conversation_id}/messages/stream",
            json=payload,
        )
        assert response.status_code == 422


@pytest.mark.asyncio
async def test_explicit_retry_creates_only_new_assistant_linked_to_old_message(
    conversation_context: ConversationContext,
) -> None:
    class RetryRagService:
        def __init__(self) -> None:
            self.calls = []

        async def stream_answer(self, knowledge_base_id, question, top_k, history, **kwargs):
            self.calls.append((question, history))
            yield StreamEvent("token", {"delta": "新回答"})
            yield StreamEvent(
                "done",
                {"citations": [], "retrieved_chunk_count": 0, "timings": {}},
            )

    service = RetryRagService()
    conversation_context.app.dependency_overrides[get_conversation_rag_service] = lambda: service
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=conversation_context.user_id,
            knowledge_base_id=conversation_context.knowledge_base_id,
            title="重试",
        )
        session.add(conversation)
        await session.flush()
        user_message = ConversationMessage(
            conversation_id=conversation.id,
            sequence_number=1,
            role="user",
            content="原问题",
            status="completed",
            completed_at=func.now(),
        )
        old_assistant = ConversationMessage(
            conversation_id=conversation.id,
            sequence_number=2,
            role="assistant",
            content="旧回答",
            status="completed",
            completed_at=func.now(),
        )
        session.add_all((user_message, old_assistant))
        await session.flush()

    response = await conversation_context.client.post(
        f"/api/v1/conversations/{conversation.id}/messages/stream",
        json={"retry_of_message_id": str(old_assistant.id)},
    )

    assert response.status_code == 200
    async with session_factory() as session:
        messages = list(
            (
                await session.scalars(
                    select(ConversationMessage)
                    .where(ConversationMessage.conversation_id == conversation.id)
                    .order_by(ConversationMessage.sequence_number)
                )
            ).all()
        )
    assert [item.role for item in messages] == ["user", "assistant", "assistant"]
    assert messages[-1].retry_of_message_id == old_assistant.id
    assert messages[-1].content == "新回答"
    assert service.calls == [("原问题", [])]


@pytest.mark.asyncio
async def test_concurrent_stream_preparation_serializes_message_sequences(
    conversation_context: ConversationContext,
) -> None:
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=conversation_context.user_id,
            knowledge_base_id=conversation_context.knowledge_base_id,
            title="并发序号",
        )
        session.add(conversation)
        await session.flush()

    pricing = ModelPricing(Decimal("0"), Decimal("0"), Decimal("0"))
    first_locked = asyncio.Event()
    release_first = asyncio.Event()

    async def prepare(question: str, *, hold_lock: bool) -> None:
        async with session_factory() as session:
            await session.execute(text("SET LOCAL lock_timeout = '5s'"))
            await session.execute(text("SET LOCAL statement_timeout = '10s'"))
            await prepare_conversation_stream(
                session,
                user_id=conversation_context.user_id,
                conversation_id=conversation.id,
                question=question,
                retry_of_message_id=None,
                model="fake",
                pricing=pricing,
                answer_input_tokens=100,
                answer_max_output_tokens=20,
            )
            if hold_lock:
                first_locked.set()
                await release_first.wait()
            await session.commit()

    first = asyncio.create_task(prepare("并发问题一", hold_lock=True))
    await asyncio.wait_for(first_locked.wait(), timeout=3)
    second = asyncio.create_task(prepare("并发问题二", hold_lock=False))
    try:
        await asyncio.sleep(0.1)
        assert second.done() is False
    finally:
        release_first.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=5)

    async with session_factory() as session:
        messages = list(
            (
                await session.scalars(
                    select(ConversationMessage)
                    .where(ConversationMessage.conversation_id == conversation.id)
                    .order_by(ConversationMessage.sequence_number)
                )
            ).all()
        )
    assert [item.sequence_number for item in messages] == [1, 2, 3, 4]
    assert [item.content for item in messages if item.role == "user"] == [
        "并发问题一",
        "并发问题二",
    ]

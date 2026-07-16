import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select, update

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db.models import (
    ADMIN_ROLE,
    USER_ROLE,
    AnswerFeedback,
    Conversation,
    ConversationMessage,
    KnowledgeBase,
    User,
)
from app.db.session import session_factory
from app.main import create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@dataclass
class FeedbackContext:
    client: httpx.AsyncClient
    other_client: httpx.AsyncClient
    user_id: UUID
    other_user_id: UUID
    knowledge_base_id: UUID
    other_knowledge_base_id: UUID


@pytest.fixture
async def feedback_context() -> AsyncIterator[FeedbackContext]:
    user = User(
        id=uuid4(),
        username=f"feedback_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    other_user = User(
        id=uuid4(),
        username=f"feedback_other_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=ADMIN_ROLE,
        is_active=True,
    )
    async with session_factory.begin() as session:
        session.add_all((user, other_user))
        await session.flush()
        knowledge_base = KnowledgeBase(name="反馈知识库", owner_id=user.id)
        other_knowledge_base = KnowledgeBase(name="他人反馈知识库", owner_id=other_user.id)
        session.add_all((knowledge_base, other_knowledge_base))

    settings = get_settings()
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with (
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={
                "Authorization": "Bearer "
                + create_access_token(user_id=user.id, role=user.role, settings=settings)
            },
        ) as client,
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={
                "Authorization": "Bearer "
                + create_access_token(
                    user_id=other_user.id,
                    role=other_user.role,
                    settings=settings,
                )
            },
        ) as other_client,
    ):
        try:
            yield FeedbackContext(
                client=client,
                other_client=other_client,
                user_id=user.id,
                other_user_id=other_user.id,
                knowledge_base_id=knowledge_base.id,
                other_knowledge_base_id=other_knowledge_base.id,
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
                    delete(ConversationMessage).where(
                        ConversationMessage.conversation_id.in_(conversation_ids)
                    )
                )
                await session.execute(
                    delete(Conversation).where(Conversation.id.in_(conversation_ids))
                )
                await session.execute(
                    delete(KnowledgeBase).where(
                        KnowledgeBase.owner_id.in_((user.id, other_user.id))
                    )
                )
                await session.execute(delete(User).where(User.id.in_((user.id, other_user.id))))


async def _completed_assistant(context: FeedbackContext) -> ConversationMessage:
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=context.user_id,
            knowledge_base_id=context.knowledge_base_id,
            title="反馈会话",
        )
        session.add(conversation)
        await session.flush()
        assistant = ConversationMessage(
            conversation_id=conversation.id,
            sequence_number=1,
            role="assistant",
            content="不得从反馈接口返回的完整答案",
            status="completed",
            completed_at=func.now(),
        )
        session.add(assistant)
        await session.flush()
        return assistant


@pytest.mark.asyncio
async def test_put_feedback_is_owner_scoped_and_idempotently_updates(
    feedback_context: FeedbackContext,
) -> None:
    assistant = await _completed_assistant(feedback_context)

    created = await feedback_context.client.put(
        f"/api/v1/messages/{assistant.id}/feedback",
        json={"helpful": True, "reason": "helpful_clear"},
    )
    updated = await feedback_context.client.put(
        f"/api/v1/messages/{assistant.id}/feedback",
        json={"helpful": False, "reason": "unhelpful_wrong"},
    )

    assert created.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["id"] == created.json()["id"]
    assert updated.json()["helpful"] is False
    assert updated.json()["reason"] == "unhelpful_wrong"
    assert "content" not in updated.json()
    async with session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(AnswerFeedback).where(AnswerFeedback.message_id == assistant.id)
                )
            ).all()
        )
    assert len(rows) == 1

    hidden = await feedback_context.other_client.put(
        f"/api/v1/messages/{assistant.id}/feedback",
        json={"helpful": True, "reason": "helpful_clear"},
    )
    missing = await feedback_context.client.put(
        f"/api/v1/messages/{uuid4()}/feedback",
        json={"helpful": True, "reason": "helpful_clear"},
    )
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"]["code"] == missing.json()["error"]["code"]


@pytest.mark.asyncio
async def test_delete_feedback_is_owner_scoped_and_missing_is_safe_404(
    feedback_context: FeedbackContext,
) -> None:
    assistant = await _completed_assistant(feedback_context)
    created = await feedback_context.client.put(
        f"/api/v1/messages/{assistant.id}/feedback",
        json={"helpful": True, "reason": "helpful_cited"},
    )
    assert created.status_code == 200

    hidden = await feedback_context.other_client.delete(f"/api/v1/messages/{assistant.id}/feedback")
    deleted = await feedback_context.client.delete(f"/api/v1/messages/{assistant.id}/feedback")
    repeated = await feedback_context.client.delete(f"/api/v1/messages/{assistant.id}/feedback")
    missing = await feedback_context.client.delete(f"/api/v1/messages/{uuid4()}/feedback")

    assert hidden.status_code == 404
    assert deleted.status_code == 204
    assert repeated.status_code == missing.status_code == 404
    assert repeated.json()["error"]["code"] == missing.json()["error"]["code"]
    async with session_factory() as session:
        assert (
            await session.scalar(
                select(AnswerFeedback).where(AnswerFeedback.message_id == assistant.id)
            )
            is None
        )


@pytest.mark.asyncio
async def test_feedback_rejects_invalid_or_helpfulness_mismatched_reason(
    feedback_context: FeedbackContext,
) -> None:
    assistant = await _completed_assistant(feedback_context)

    invalid = await feedback_context.client.put(
        f"/api/v1/messages/{assistant.id}/feedback",
        json={"helpful": True, "reason": "invented_reason"},
    )
    helpful_mismatch = await feedback_context.client.put(
        f"/api/v1/messages/{assistant.id}/feedback",
        json={"helpful": True, "reason": "unhelpful_wrong"},
    )
    unhelpful_mismatch = await feedback_context.client.put(
        f"/api/v1/messages/{assistant.id}/feedback",
        json={"helpful": False, "reason": "helpful_clear"},
    )

    assert invalid.status_code == 422
    assert helpful_mismatch.status_code == 422
    assert unhelpful_mismatch.status_code == 422


@pytest.mark.asyncio
async def test_me_feedback_is_owner_scoped_stably_paginated_and_contains_no_content(
    feedback_context: FeedbackContext,
) -> None:
    assistants = [await _completed_assistant(feedback_context) for _ in range(3)]
    for index, assistant in enumerate(assistants):
        response = await feedback_context.client.put(
            f"/api/v1/messages/{assistant.id}/feedback",
            json={
                "helpful": index % 2 == 0,
                "reason": "helpful_clear" if index % 2 == 0 else "unhelpful_unclear",
            },
        )
        assert response.status_code == 200
    async with session_factory.begin() as session:
        feedback_rows = list(
            (
                await session.scalars(
                    select(AnswerFeedback).where(
                        AnswerFeedback.message_id.in_([item.id for item in assistants])
                    )
                )
            ).all()
        )
        base = datetime(2026, 7, 16, 8, tzinfo=UTC)
        for index, feedback in enumerate(feedback_rows):
            await session.execute(
                update(AnswerFeedback)
                .where(AnswerFeedback.id == feedback.id)
                .values(updated_at=base + timedelta(seconds=index))
            )
        other_conversation = Conversation(
            user_id=feedback_context.other_user_id,
            knowledge_base_id=feedback_context.other_knowledge_base_id,
            title="他人会话标题",
        )
        session.add(other_conversation)
        await session.flush()
        other_assistant = ConversationMessage(
            conversation_id=other_conversation.id,
            sequence_number=1,
            role="assistant",
            content="他人答案正文",
            status="completed",
            completed_at=func.now(),
        )
        session.add(other_assistant)
        await session.flush()
        session.add(
            AnswerFeedback(
                user_id=feedback_context.other_user_id,
                message_id=other_assistant.id,
                helpful=True,
                reason="helpful_clear",
            )
        )
    expected_ids = [
        str(item.id)
        for item in sorted(
            feedback_rows,
            key=lambda item: (item.updated_at, item.id),
            reverse=True,
        )
    ]

    first = await feedback_context.client.get(
        "/api/v1/me/feedback", params={"page": 1, "page_size": 2}
    )
    second = await feedback_context.client.get(
        "/api/v1/me/feedback", params={"page": 2, "page_size": 2}
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["total"] == second.json()["total"] == 3
    assert first.json()["page"] == 1
    assert second.json()["page"] == 2
    assert first.json()["page_size"] == second.json()["page_size"] == 2
    assert [item["id"] for item in first.json()["items"]] == expected_ids[:2]
    assert [item["id"] for item in second.json()["items"]] == expected_ids[2:]
    serialized = repr(first.json()) + repr(second.json())
    for forbidden in (
        "content",
        "question",
        "answer",
        "file_name",
        "knowledge_base_name",
        "不得从反馈接口返回的完整答案",
        "反馈知识库",
    ):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_feedback_requires_completed_assistant_in_active_owned_knowledge_base(
    feedback_context: FeedbackContext,
) -> None:
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=feedback_context.user_id,
            knowledge_base_id=feedback_context.knowledge_base_id,
            title="状态限制",
        )
        session.add(conversation)
        await session.flush()
        messages = [
            ConversationMessage(
                conversation_id=conversation.id,
                sequence_number=1,
                role="user",
                content="用户消息",
                status="completed",
                completed_at=func.now(),
            ),
            ConversationMessage(
                conversation_id=conversation.id,
                sequence_number=2,
                role="assistant",
                content="流式中",
                status="streaming",
            ),
            ConversationMessage(
                conversation_id=conversation.id,
                sequence_number=3,
                role="assistant",
                content="已中断",
                status="interrupted",
                completed_at=func.now(),
            ),
            ConversationMessage(
                conversation_id=conversation.id,
                sequence_number=4,
                role="assistant",
                content="已失败",
                status="failed",
                completed_at=func.now(),
            ),
        ]
        deleted_knowledge_base = KnowledgeBase(
            name="软删反馈知识库",
            owner_id=feedback_context.user_id,
            deleted_at=func.now(),
        )
        session.add_all((*messages, deleted_knowledge_base))
        await session.flush()
        deleted_conversation = Conversation(
            user_id=feedback_context.user_id,
            knowledge_base_id=deleted_knowledge_base.id,
            title="软删资源会话",
        )
        session.add(deleted_conversation)
        await session.flush()
        deleted_assistant = ConversationMessage(
            conversation_id=deleted_conversation.id,
            sequence_number=1,
            role="assistant",
            content="软删资源答案",
            status="completed",
            completed_at=func.now(),
        )
        session.add(deleted_assistant)
        await session.flush()

    for message_id in [*(item.id for item in messages), deleted_assistant.id]:
        response = await feedback_context.client.put(
            f"/api/v1/messages/{message_id}/feedback",
            json={"helpful": True, "reason": "helpful_clear"},
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "MESSAGE_NOT_FOUND"


@pytest.mark.asyncio
async def test_concurrent_feedback_puts_keep_one_row(
    feedback_context: FeedbackContext,
) -> None:
    assistant = await _completed_assistant(feedback_context)

    first, second = await asyncio.gather(
        feedback_context.client.put(
            f"/api/v1/messages/{assistant.id}/feedback",
            json={"helpful": True, "reason": "helpful_clear"},
        ),
        feedback_context.client.put(
            f"/api/v1/messages/{assistant.id}/feedback",
            json={"helpful": False, "reason": "unhelpful_wrong"},
        ),
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    async with session_factory() as session:
        rows = list(
            (
                await session.scalars(
                    select(AnswerFeedback).where(AnswerFeedback.message_id == assistant.id)
                )
            ).all()
        )
    assert len(rows) == 1
    assert (rows[0].helpful, rows[0].reason) in {
        (True, "helpful_clear"),
        (False, "unhelpful_wrong"),
    }


@pytest.mark.asyncio
async def test_soft_deleted_knowledge_base_hides_existing_feedback(
    feedback_context: FeedbackContext,
) -> None:
    assistant = await _completed_assistant(feedback_context)
    created = await feedback_context.client.put(
        f"/api/v1/messages/{assistant.id}/feedback",
        json={"helpful": True, "reason": "helpful_clear"},
    )
    assert created.status_code == 200
    async with session_factory.begin() as session:
        await session.execute(
            update(KnowledgeBase)
            .where(KnowledgeBase.id == feedback_context.knowledge_base_id)
            .values(deleted_at=func.now())
        )

    page = await feedback_context.client.get("/api/v1/me/feedback")
    deletion = await feedback_context.client.delete(f"/api/v1/messages/{assistant.id}/feedback")

    assert page.status_code == 200
    assert page.json()["total"] == 0
    assert page.json()["items"] == []
    assert deletion.status_code == 404

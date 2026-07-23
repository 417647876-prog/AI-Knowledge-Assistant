import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, func, select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db.models import (
    USER_ROLE,
    Conversation,
    ConversationMessage,
    KnowledgeBase,
    LlmUsageEvent,
    QualityEvaluationRun,
    User,
)
from app.db.session import session_factory
from app.main import create_app
from scripts.record_quality_evaluation import (
    QualityEvaluationSummary,
    record_quality_evaluation,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@dataclass
class UsageContext:
    client: httpx.AsyncClient
    user_id: UUID
    other_user_id: UUID
    knowledge_base_id: UUID
    other_knowledge_base_id: UUID


@pytest.fixture
async def usage_context() -> AsyncIterator[UsageContext]:
    user = User(
        id=uuid4(),
        username=f"usage_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    other_user = User(
        id=uuid4(),
        username=f"usage_other_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    async with session_factory.begin() as session:
        session.add_all((user, other_user))
        await session.flush()
        knowledge_base = KnowledgeBase(name="用量知识库", owner_id=user.id)
        other_knowledge_base = KnowledgeBase(name="他人用量知识库", owner_id=other_user.id)
        session.add_all((knowledge_base, other_knowledge_base))

    settings = get_settings()
    app = create_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": "Bearer "
            + create_access_token(user_id=user.id, role=user.role, settings=settings)
        },
    ) as client:
        try:
            yield UsageContext(
                client=client,
                user_id=user.id,
                other_user_id=other_user.id,
                knowledge_base_id=knowledge_base.id,
                other_knowledge_base_id=other_knowledge_base.id,
            )
        finally:
            async with session_factory.begin() as session:
                await session.execute(
                    delete(LlmUsageEvent).where(LlmUsageEvent.user_id.in_((user.id, other_user.id)))
                )
                conversation_ids = select(Conversation.id).where(
                    Conversation.user_id.in_((user.id, other_user.id))
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


async def _add_usage(
    *,
    user_id: UUID,
    knowledge_base_id: UUID,
    created_at: datetime,
    purpose: str,
    status: str,
    usage_complete: bool,
    tokens: tuple[int, int, int, int],
    settled_cost: Decimal,
) -> None:
    cache_hit, cache_miss, output, reasoning = tokens
    async with session_factory.begin() as session:
        conversation = Conversation(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            title="不得从用量摘要返回的会话标题",
        )
        session.add(conversation)
        await session.flush()
        assistant = ConversationMessage(
            conversation_id=conversation.id,
            sequence_number=1,
            role="assistant",
            content="不得从用量摘要返回的答案正文",
            status="completed",
            completed_at=func.now(),
        )
        session.add(assistant)
        await session.flush()
        session.add(
            LlmUsageEvent(
                user_id=user_id,
                knowledge_base_id=knowledge_base_id,
                conversation_id=conversation.id,
                message_id=assistant.id,
                purpose=purpose,
                status=status,
                model="sensitive-model-name",
                cache_hit_input_tokens=cache_hit,
                cache_miss_input_tokens=cache_miss,
                output_tokens=output,
                reasoning_tokens=reasoning,
                total_tokens=cache_hit + cache_miss + output,
                usage_complete=usage_complete,
                price_snapshot={"sensitive": "provider pricing"},
                reserved_cost=settled_cost,
                settled_cost=None if status == "reserved" else settled_cost,
                created_at=created_at,
                completed_at=None if status == "reserved" else created_at,
            )
        )


@pytest.mark.asyncio
async def test_usage_summary_is_owner_scoped_uses_real_usage_and_utc_datetime_boundaries(
    usage_context: UsageContext,
) -> None:
    await _add_usage(
        user_id=usage_context.user_id,
        knowledge_base_id=usage_context.knowledge_base_id,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        purpose="answer",
        status="succeeded",
        usage_complete=True,
        tokens=(2, 3, 5, 1),
        settled_cost=Decimal("0.123456"),
    )
    await _add_usage(
        user_id=usage_context.user_id,
        knowledge_base_id=usage_context.knowledge_base_id,
        created_at=datetime(2026, 7, 2, 23, 59, 59, tzinfo=UTC),
        purpose="rewrite",
        status="usage_unknown",
        usage_complete=False,
        tokens=(0, 0, 0, 0),
        settled_cost=Decimal("0.010000"),
    )
    await _add_usage(
        user_id=usage_context.user_id,
        knowledge_base_id=usage_context.knowledge_base_id,
        created_at=datetime(2026, 7, 3, tzinfo=UTC),
        purpose="answer",
        status="succeeded",
        usage_complete=True,
        tokens=(100, 100, 100, 0),
        settled_cost=Decimal("9.000000"),
    )
    await _add_usage(
        user_id=usage_context.other_user_id,
        knowledge_base_id=usage_context.other_knowledge_base_id,
        created_at=datetime(2026, 7, 1, 12, tzinfo=UTC),
        purpose="answer",
        status="succeeded",
        usage_complete=True,
        tokens=(1000, 1000, 1000, 0),
        settled_cost=Decimal("99.000000"),
    )

    response = await usage_context.client.get(
        "/api/v1/me/usage",
        params={
            "from": "2026-07-01T00:00:00Z",
            "to": "2026-07-03T00:00:00Z",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "from": "2026-07-01T00:00:00Z",
        "to": "2026-07-03T00:00:00Z",
        "tokens": {
            "cache_hit_input_tokens": 2,
            "cache_miss_input_tokens": 3,
            "output_tokens": 5,
            "reasoning_tokens": 1,
            "total_tokens": 10,
        },
        "estimated_cost": "0.133456",
        "usage_unknown_count": 1,
        "purposes": {
            "answer": {
                "event_count": 1,
                "total_tokens": 10,
                "estimated_cost": "0.123456",
                "usage_unknown_count": 0,
            },
            "rewrite": {
                "event_count": 1,
                "total_tokens": 0,
                "estimated_cost": "0.010000",
                "usage_unknown_count": 1,
            },
        },
    }


@pytest.mark.asyncio
async def test_quality_evaluation_same_report_hash_is_idempotent_in_real_database() -> None:
    report_hash = "e" * 64
    completed_at = datetime(2026, 7, 16, 8, tzinfo=UTC)
    summary = QualityEvaluationSummary(
        dataset_hash="f" * 64,
        mode="rewrite",
        model_config_summary={"app_env": "test"},
        metrics={"case_count": 30},
        report_hash=report_hash,
        gate_passed=True,
        started_at=completed_at,
        completed_at=completed_at,
        duration_ms=0,
    )
    try:
        async with session_factory.begin() as session:
            first = await record_quality_evaluation(session, summary)
            second = await record_quality_evaluation(session, summary)
            assert first.id == second.id
        async with session_factory() as session:
            count = await session.scalar(
                select(func.count())
                .select_from(QualityEvaluationRun)
                .where(QualityEvaluationRun.report_hash == report_hash)
            )
        assert count == 1
    finally:
        async with session_factory.begin() as session:
            await session.execute(
                delete(QualityEvaluationRun).where(QualityEvaluationRun.report_hash == report_hash)
            )


@pytest.mark.asyncio
async def test_usage_summary_rejects_invalid_ranges_normalizes_utc_and_ignores_reserved(
    usage_context: UsageContext,
) -> None:
    await _add_usage(
        user_id=usage_context.user_id,
        knowledge_base_id=usage_context.knowledge_base_id,
        created_at=datetime(2026, 7, 1, 0, 15, tzinfo=UTC),
        purpose="rewrite",
        status="reserved",
        usage_complete=False,
        tokens=(0, 0, 0, 0),
        settled_cost=Decimal("9.000000"),
    )
    await _add_usage(
        user_id=usage_context.user_id,
        knowledge_base_id=usage_context.knowledge_base_id,
        created_at=datetime(2026, 7, 1, 0, 30, tzinfo=UTC),
        purpose="answer",
        status="failed_after_request",
        usage_complete=False,
        tokens=(0, 0, 0, 0),
        settled_cost=Decimal("0.020000"),
    )

    for params in (
        {"from": "2026-07-01T00:00:00", "to": "2026-07-01T01:00:00Z"},
        {"from": "2026-07-01T00:00:00Z", "to": "2026-07-01T00:00:00Z"},
        {"from": "2026-07-01T01:00:00Z", "to": "2026-07-01T00:00:00Z"},
    ):
        response = await usage_context.client.get("/api/v1/me/usage", params=params)
        assert response.status_code == 422

    response = await usage_context.client.get(
        "/api/v1/me/usage",
        params={
            "from": "2026-07-01T08:00:00+08:00",
            "to": "2026-07-01T09:00:00+08:00",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["from"] == "2026-07-01T00:00:00Z"
    assert body["to"] == "2026-07-01T01:00:00Z"
    assert body["estimated_cost"] == "0.020000"
    assert body["usage_unknown_count"] == 0
    assert body["purposes"] == {
        "answer": {
            "event_count": 1,
            "total_tokens": 0,
            "estimated_cost": "0.020000",
            "usage_unknown_count": 0,
        },
        "rewrite": {
            "event_count": 0,
            "total_tokens": 0,
            "estimated_cost": "0.000000",
            "usage_unknown_count": 0,
        },
    }

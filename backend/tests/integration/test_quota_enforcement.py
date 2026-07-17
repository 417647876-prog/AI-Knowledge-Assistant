import asyncio
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token, hash_password
from app.db.models import (
    ADMIN_ROLE,
    USER_ROLE,
    Conversation,
    ConversationMessage,
    Document,
    KnowledgeBase,
    LlmUsageEvent,
    User,
    UserQuota,
)
from app.db.session import session_factory
from app.main import create_app
from app.quotas.service import QuotaDefaults, consume_question, consume_upload, reserve_global_cost

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@dataclass(frozen=True)
class UsageScope:
    user: User
    knowledge_base: KnowledgeBase
    conversation: Conversation
    assistant_message: ConversationMessage


def _defaults(
    *, questions: int = 50, uploads: int = 20, storage: int = 500 * 1024**2
) -> QuotaDefaults:
    return QuotaDefaults(daily_questions=questions, daily_uploads=uploads, storage_bytes=storage)


async def _create_usage_scope(*, role: str = USER_ROLE) -> UsageScope:
    user = User(
        id=uuid4(),
        username=f"quota_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=role,
        is_active=True,
    )
    knowledge_base = KnowledgeBase(id=uuid4(), name="额度测试库", owner_id=user.id)
    conversation = Conversation(
        id=uuid4(), user_id=user.id, knowledge_base_id=knowledge_base.id, title="额度测试会话"
    )
    message = ConversationMessage(
        id=uuid4(),
        conversation_id=conversation.id,
        sequence_number=1,
        role="assistant",
        content="",
        status="streaming",
    )
    async with session_factory.begin() as session:
        session.add_all((user, knowledge_base, conversation, message))
    return UsageScope(user, knowledge_base, conversation, message)


async def _delete_usage_scope(scope: UsageScope) -> None:
    async with session_factory.begin() as session:
        await session.execute(
            delete(LlmUsageEvent).where(LlmUsageEvent.conversation_id == scope.conversation.id)
        )
        await session.execute(
            delete(ConversationMessage).where(
                ConversationMessage.conversation_id == scope.conversation.id
            )
        )
        await session.execute(delete(Conversation).where(Conversation.id == scope.conversation.id))
        await session.execute(delete(Document).where(Document.uploaded_by_user_id == scope.user.id))
        await session.execute(delete(UserQuota).where(UserQuota.user_id == scope.user.id))
        await session.execute(
            delete(KnowledgeBase).where(KnowledgeBase.id == scope.knowledge_base.id)
        )
        await session.execute(delete(User).where(User.id == scope.user.id))


def _reserved_event(scope: UsageScope, cost: Decimal) -> LlmUsageEvent:
    return LlmUsageEvent(
        id=uuid4(),
        user_id=scope.user.id,
        knowledge_base_id=scope.knowledge_base.id,
        conversation_id=scope.conversation.id,
        message_id=scope.assistant_message.id,
        purpose="answer",
        status="reserved",
        model="quota-test",
        reserved_cost=cost,
    )


@pytest.mark.asyncio
async def test_daily_question_quota_resets_in_shanghai_day_and_is_atomic() -> None:
    scope = await _create_usage_scope()
    defaults = _defaults(questions=1)
    first_day = date(2026, 7, 17)
    try:
        async with session_factory.begin() as session:
            await consume_question(
                session, user_id=scope.user.id, defaults=defaults, today=first_day
            )
        async with session_factory.begin() as session:
            await consume_question(
                session, user_id=scope.user.id, defaults=defaults, today=date(2026, 7, 18)
            )

        async def consume_once() -> str:
            try:
                async with session_factory.begin() as session:
                    await consume_question(
                        session,
                        user_id=scope.user.id,
                        defaults=defaults,
                        today=date(2026, 7, 19),
                    )
                return "success"
            except AppError as error:
                assert error.code == "QUESTION_QUOTA_EXCEEDED"
                return error.code

        results = await asyncio.gather(consume_once(), consume_once())
        assert results.count("success") == 1
        assert results.count("QUESTION_QUOTA_EXCEEDED") == 1
    finally:
        await _delete_usage_scope(scope)


@pytest.mark.asyncio
async def test_inactive_user_cannot_consume_quota() -> None:
    scope = await _create_usage_scope()
    try:
        async with session_factory.begin() as session:
            stored = await session.get(User, scope.user.id, with_for_update=True)
            assert stored is not None
            stored.is_active = False
        with pytest.raises(AppError, match="账号已停用") as raised:
            async with session_factory.begin() as session:
                await consume_question(session, user_id=scope.user.id, defaults=_defaults())
        assert raised.value.code == "AUTHENTICATION_REQUIRED"
        async with session_factory() as session:
            assert await session.get(UserQuota, scope.user.id) is None
    finally:
        await _delete_usage_scope(scope)


@pytest.mark.asyncio
async def test_upload_uses_read_bytes_and_only_effective_storage_counts(
    tmp_path: Path,
) -> None:
    scope = await _create_usage_scope()
    settings = get_settings()
    previous_directory = settings.upload_directory
    previous_storage_limit = settings.default_storage_bytes_limit
    try:
        settings.upload_directory = tmp_path
        settings.default_storage_bytes_limit = 3
        token = create_access_token(user_id=scope.user.id, role=scope.user.role, settings=settings)
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            rejected = await client.post(
                f"/api/v1/knowledge-bases/{scope.knowledge_base.id}/documents",
                files={"file": ("too-large.txt", b"1234", "text/plain")},
            )
        assert rejected.status_code == 429
        assert rejected.json()["error"]["code"] == "STORAGE_QUOTA_EXCEEDED"
        assert list(tmp_path.iterdir()) == []

        async with session_factory.begin() as session:
            session.add_all(
                (
                    Document(
                        knowledge_base_id=scope.knowledge_base.id,
                        uploaded_by_user_id=scope.user.id,
                        original_file_name="failed.txt",
                        stored_file_name="failed.txt",
                        content_type="text/plain",
                        file_extension=".txt",
                        file_size=50,
                        file_hash="f" * 64,
                        status="failed",
                    ),
                    Document(
                        knowledge_base_id=scope.knowledge_base.id,
                        uploaded_by_user_id=scope.user.id,
                        original_file_name="trash.txt",
                        stored_file_name="trash.txt",
                        content_type="text/plain",
                        file_extension=".txt",
                        file_size=50,
                        file_hash="t" * 64,
                        deleted_at=datetime.now(UTC),
                    ),
                )
            )
        async with session_factory.begin() as session:
            await consume_upload(
                session,
                user_id=scope.user.id,
                content_bytes=3,
                defaults=_defaults(storage=3),
            )
    finally:
        settings.upload_directory = previous_directory
        settings.default_storage_bytes_limit = previous_storage_limit
        await _delete_usage_scope(scope)


@pytest.mark.asyncio
async def test_admin_personal_quota_override_cannot_bypass_global_cost_limit() -> None:
    scope = await _create_usage_scope(role=ADMIN_ROLE)
    try:
        async with session_factory.begin() as session:
            session.add(UserQuota(user_id=scope.user.id, daily_question_limit=999_999))
            session.add(_reserved_event(scope, Decimal("20.000000")))
        with pytest.raises(AppError) as raised:
            async with session_factory.begin() as session:
                await reserve_global_cost(
                    session,
                    new_cost=Decimal("0.000001"),
                    limit=Decimal("20.00"),
                )
        assert raised.value.code == "GLOBAL_COST_LIMIT_REACHED"
    finally:
        await _delete_usage_scope(scope)


@pytest.mark.asyncio
async def test_global_cost_advisory_lock_allows_only_one_concurrent_reservation() -> None:
    scope = await _create_usage_scope()

    async def reserve_once() -> str:
        try:
            async with session_factory.begin() as session:
                await reserve_global_cost(
                    session,
                    new_cost=Decimal("11.000000"),
                    limit=Decimal("20.00"),
                )
                session.add(_reserved_event(scope, Decimal("11.000000")))
            return "success"
        except AppError as error:
            assert error.code == "GLOBAL_COST_LIMIT_REACHED"
            return error.code

    try:
        results = await asyncio.gather(reserve_once(), reserve_once())
        assert results.count("success") == 1
        assert results.count("GLOBAL_COST_LIMIT_REACHED") == 1
        async with session_factory() as session:
            reserved_total = await session.scalar(
                select(LlmUsageEvent.reserved_cost).where(
                    LlmUsageEvent.conversation_id == scope.conversation.id
                )
            )
        assert reserved_total == Decimal("11.000000")
    finally:
        await _delete_usage_scope(scope)

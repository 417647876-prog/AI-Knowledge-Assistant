import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text
from sqlalchemy.exc import DBAPIError

from app.api.v1.admin_users import AdminQuotaUpdate, AdminUserUpdate, update_user, update_user_quota
from app.api.v1.support_grants import SupportGrantCreate, create_support_grant
from app.core.config import Settings
from app.db.models import (
    AuditEvent,
    Document,
    DocumentJob,
    KnowledgeBase,
    SupportAccessGrant,
    User,
    UserQuota,
)
from app.db.session import session_factory
from app.lifecycle.service import request_purge_document
from tests.database_cleanup import delete_owned_knowledge_bases

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@pytest.fixture
async def audit_users() -> AsyncIterator[tuple[User, User]]:
    admin = User(username=f"audit_admin_{uuid4().hex}", password_hash="unused", role="admin")
    target = User(username=f"audit_target_{uuid4().hex}", password_hash="unused", role="user")
    async with session_factory.begin() as session:
        session.add_all([admin, target])
    try:
        yield admin, target
    finally:
        async with session_factory.begin() as session:
            await session.execute(
                delete(AuditEvent).where(AuditEvent.actor_user_id.in_([admin.id, target.id]))
            )
            await delete_owned_knowledge_bases(session, [admin.id, target.id])
            await session.execute(delete(User).where(User.id.in_([admin.id, target.id])))


async def _install_audit_failure() -> None:
    async with session_factory.begin() as session:
        await session.execute(
            text(
                "CREATE OR REPLACE FUNCTION task12_fail_audit_insert() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'task12 audit failure'; END; $$"
            )
        )
        await session.execute(
            text(
                "CREATE TRIGGER task12_fail_audit_insert BEFORE INSERT ON audit_events "
                "FOR EACH ROW EXECUTE FUNCTION task12_fail_audit_insert()"
            )
        )


async def _remove_audit_failure() -> None:
    async with session_factory.begin() as session:
        await session.execute(
            text("DROP TRIGGER IF EXISTS task12_fail_audit_insert ON audit_events")
        )
        await session.execute(text("DROP FUNCTION IF EXISTS task12_fail_audit_insert()"))


@pytest.mark.asyncio
async def test_audit_insert_failure_rolls_back_user_disable_and_quota_change(
    audit_users: tuple[User, User],
) -> None:
    admin, target = audit_users
    await _install_audit_failure()
    try:
        async with session_factory() as session:
            with pytest.raises(DBAPIError):
                await update_user(
                    user_id=target.id,
                    payload=AdminUserUpdate(is_active=False),
                    admin=admin,
                    session=session,
                    settings=Settings(_env_file=None),
                )
            await session.rollback()
        async with session_factory() as session:
            stored = await session.get(User, target.id)
            assert stored is not None and stored.is_active is True

        async with session_factory() as session:
            with pytest.raises(DBAPIError):
                await update_user_quota(
                    user_id=target.id,
                    payload=AdminQuotaUpdate(daily_question_limit=1),
                    admin=admin,
                    session=session,
                )
            await session.rollback()
        async with session_factory() as session:
            assert await session.get(UserQuota, target.id) is None
    finally:
        await _remove_audit_failure()


@pytest.mark.asyncio
async def test_audit_insert_failure_rolls_back_support_grant_and_purge_request(
    audit_users: tuple[User, User],
) -> None:
    admin, owner = audit_users
    now = datetime.now(UTC)
    async with session_factory.begin() as session:
        knowledge_base = KnowledgeBase(name=f"audit-{uuid4().hex}", owner_id=owner.id)
        session.add(knowledge_base)
        await session.flush()
        document = Document(
            knowledge_base_id=knowledge_base.id,
            uploaded_by_user_id=owner.id,
            original_file_name="safe.txt",
            stored_file_name=f"{uuid4()}.txt",
            content_type="text/plain",
            file_extension=".txt",
            file_size=1,
            file_hash=uuid4().hex * 2,
            deleted_at=now - timedelta(days=8),
            purge_after=now - timedelta(days=1),
        )
        session.add(document)
    await _install_audit_failure()
    try:
        async with session_factory() as session:
            with pytest.raises(DBAPIError):
                await create_support_grant(
                    knowledge_base_id=knowledge_base.id,
                    payload=SupportGrantCreate(admin_user_id=admin.id),
                    session=session,
                    current_user=owner,
                )
            await session.rollback()
        async with session_factory() as session:
            grants = await session.scalars(
                select(SupportAccessGrant).where(
                    SupportAccessGrant.knowledge_base_id == knowledge_base.id
                )
            )
            assert list(grants) == []

        async with session_factory() as session:
            await request_purge_document(
                session,
                owner_user_id=owner.id,
                document_id=document.id,
                max_attempts=1,
            )
            with pytest.raises(DBAPIError):
                await session.commit()
            await session.rollback()
        async with session_factory() as session:
            jobs = await session.scalars(
                select(DocumentJob).where(DocumentJob.resource_id == document.id)
            )
            assert list(jobs) == []
    finally:
        await _remove_audit_failure()

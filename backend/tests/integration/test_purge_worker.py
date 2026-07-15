import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.core.exceptions import AppError
from app.core.security import hash_password
from app.db.models import (
    ADMIN_ROLE,
    USER_ROLE,
    AuditEvent,
    Document,
    DocumentChunk,
    DocumentJob,
    KnowledgeBase,
    SupportAccessGrant,
    User,
)
from app.db.session import session_factory
from app.jobs.repository import claim_next_job
from app.lifecycle.service import purge_document, purge_knowledge_base
from tests.database_cleanup import delete_owned_knowledge_bases

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@pytest.mark.asyncio
async def test_purge_document_deletes_file_and_content_but_keeps_safe_audit(tmp_path) -> None:
    user = User(
        id=uuid4(),
        username=f"purge_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    stored_name = f"{uuid4()}.txt"
    stored_file = tmp_path / stored_name
    stored_file.write_text("private body", encoding="utf-8")
    try:
        async with session_factory.begin() as session:
            session.add(user)
            await session.flush()
            knowledge_base = KnowledgeBase(name="purge", owner_id=user.id)
            session.add(knowledge_base)
            await session.flush()
            deleted_at = datetime.now(UTC) - timedelta(days=8)
            document = Document(
                knowledge_base_id=knowledge_base.id,
                uploaded_by_user_id=user.id,
                original_file_name="private.txt",
                stored_file_name=stored_name,
                content_type="text/plain",
                file_extension=".txt",
                file_size=12,
                file_hash="7" * 64,
                status="ready",
                deleted_at=deleted_at,
                purge_after=deleted_at + timedelta(days=7),
            )
            session.add(document)
            await session.flush()
            session.add(
                DocumentChunk(
                    document_id=document.id,
                    knowledge_base_id=knowledge_base.id,
                    chunk_index=0,
                    content="private body",
                    content_hash="6" * 64,
                    embedding=[0.0] * 512,
                )
            )
            from app.jobs.repository import enqueue_job

            job = await enqueue_job(
                session,
                job_type="purge_document",
                resource_type="document",
                resource_id=document.id,
                owner_user_id=user.id,
                knowledge_base_id=knowledge_base.id,
            )
        async with session_factory.begin() as session:
            lease = await claim_next_job(
                session,
                worker_id="purge-test",
                now=datetime.now(UTC),
                lease_seconds=120,
            )
        assert lease is not None and lease.job_id == job.id

        await purge_document(
            session_factory=session_factory,
            upload_directory=tmp_path,
            lease=lease,
        )
        await purge_document(
            session_factory=session_factory,
            upload_directory=tmp_path,
            lease=lease,
        )

        assert not stored_file.exists()
        async with session_factory() as session:
            assert await session.get(Document, document.id) is None
            assert (
                await session.scalar(
                    select(DocumentChunk.id).where(DocumentChunk.document_id == document.id)
                )
            ) is None
            event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.resource_id == document.id,
                    AuditEvent.action == "document.purge",
                )
            )
        assert event is not None
        assert "private" not in str(event.security_summary)
    finally:
        async with session_factory.begin() as session:
            await delete_owned_knowledge_bases(session, [user.id])
            await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == user.id))
            await session.execute(delete(User).where(User.id == user.id))


@pytest.mark.asyncio
async def test_purge_knowledge_base_handles_missing_child_file_and_removes_dependencies(
    tmp_path,
) -> None:
    owner = User(
        id=uuid4(),
        username=f"purge_owner_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    admin = User(
        id=uuid4(),
        username=f"purge_admin_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=ADMIN_ROLE,
        is_active=True,
    )
    existing_name = f"{uuid4()}.txt"
    (tmp_path / existing_name).write_text("existing private body", encoding="utf-8")
    missing_name = f"{uuid4()}.txt"
    try:
        async with session_factory.begin() as session:
            session.add_all([owner, admin])
            await session.flush()
            deleted_at = datetime.now(UTC) - timedelta(days=8)
            knowledge_base = KnowledgeBase(
                name="purge kb",
                owner_id=owner.id,
                deleted_at=deleted_at,
                purge_after=deleted_at + timedelta(days=7),
            )
            session.add(knowledge_base)
            await session.flush()
            documents = []
            for stored_name in (existing_name, missing_name):
                document = Document(
                    knowledge_base_id=knowledge_base.id,
                    uploaded_by_user_id=owner.id,
                    original_file_name="private.txt",
                    stored_file_name=stored_name,
                    content_type="text/plain",
                    file_extension=".txt",
                    file_size=12,
                    file_hash=uuid4().hex * 2,
                    status="ready",
                    deleted_at=deleted_at,
                    purge_after=deleted_at + timedelta(days=7),
                )
                session.add(document)
                await session.flush()
                documents.append(document)
                session.add(
                    DocumentChunk(
                        document_id=document.id,
                        knowledge_base_id=knowledge_base.id,
                        chunk_index=0,
                        content="private body",
                        content_hash=uuid4().hex * 2,
                        embedding=[0.0] * 512,
                    )
                )
            session.add(
                SupportAccessGrant(
                    knowledge_base_id=knowledge_base.id,
                    owner_user_id=owner.id,
                    admin_user_id=admin.id,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            from app.jobs.repository import enqueue_job

            job = await enqueue_job(
                session,
                job_type="purge_knowledge_base",
                resource_type="knowledge_base",
                resource_id=knowledge_base.id,
                owner_user_id=owner.id,
                knowledge_base_id=knowledge_base.id,
            )
        async with session_factory.begin() as session:
            lease = await claim_next_job(
                session,
                worker_id="purge-kb-test",
                now=datetime.now(UTC),
                lease_seconds=120,
            )
        assert lease is not None and lease.job_id == job.id

        await purge_knowledge_base(
            session_factory=session_factory,
            upload_directory=tmp_path,
            lease=lease,
        )
        await purge_knowledge_base(
            session_factory=session_factory,
            upload_directory=tmp_path,
            lease=lease,
        )

        assert not (tmp_path / existing_name).exists()
        async with session_factory() as session:
            assert await session.get(KnowledgeBase, knowledge_base.id) is None
            assert (
                await session.scalar(
                    select(Document.id).where(Document.knowledge_base_id == knowledge_base.id)
                )
            ) is None
            assert (
                await session.scalar(
                    select(DocumentJob.id).where(DocumentJob.knowledge_base_id == knowledge_base.id)
                )
            ) is None
            assert (
                await session.scalar(
                    select(SupportAccessGrant.id).where(
                        SupportAccessGrant.knowledge_base_id == knowledge_base.id
                    )
                )
            ) is None
            event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.resource_id == knowledge_base.id,
                    AuditEvent.action == "knowledge_base.purge",
                )
            )
        assert event is not None
        assert "private" not in str(event.security_summary)
    finally:
        async with session_factory.begin() as session:
            await delete_owned_knowledge_bases(session, [owner.id])
            await session.execute(
                delete(AuditEvent).where(AuditEvent.actor_user_id.in_([owner.id, admin.id]))
            )
            await session.execute(delete(User).where(User.id.in_([owner.id, admin.id])))


@pytest.mark.asyncio
async def test_purge_rejects_path_escape_without_deleting_and_audits_denial(tmp_path) -> None:
    user = User(
        id=uuid4(),
        username=f"purge_path_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    outside_name = f"outside-{uuid4()}.txt"
    outside_file = tmp_path.parent / outside_name
    outside_file.write_text("must survive", encoding="utf-8")
    try:
        async with session_factory.begin() as session:
            session.add(user)
            await session.flush()
            knowledge_base = KnowledgeBase(name="path guard", owner_id=user.id)
            session.add(knowledge_base)
            await session.flush()
            deleted_at = datetime.now(UTC) - timedelta(days=8)
            document = Document(
                knowledge_base_id=knowledge_base.id,
                uploaded_by_user_id=user.id,
                original_file_name="private.txt",
                stored_file_name=f"../{outside_name}",
                content_type="text/plain",
                file_extension=".txt",
                file_size=12,
                file_hash=uuid4().hex * 2,
                status="ready",
                deleted_at=deleted_at,
                purge_after=deleted_at + timedelta(days=7),
            )
            session.add(document)
            await session.flush()
            from app.jobs.repository import enqueue_job

            job = await enqueue_job(
                session,
                job_type="purge_document",
                resource_type="document",
                resource_id=document.id,
                owner_user_id=user.id,
                knowledge_base_id=knowledge_base.id,
            )
        async with session_factory.begin() as session:
            lease = await claim_next_job(
                session,
                worker_id="purge-path-test",
                now=datetime.now(UTC),
                lease_seconds=120,
            )
        assert lease is not None and lease.job_id == job.id

        with pytest.raises(AppError) as captured:
            await purge_document(
                session_factory=session_factory,
                upload_directory=tmp_path,
                lease=lease,
            )

        assert captured.value.code == "PURGE_PATH_INVALID"
        assert outside_file.exists()
        async with session_factory() as session:
            event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.resource_id == document.id,
                    AuditEvent.action == "document.purge",
                    AuditEvent.result == "denied",
                )
            )
        assert event is not None
        assert event.security_summary == {"reason": "PURGE_PATH_INVALID"}
    finally:
        outside_file.unlink(missing_ok=True)
        async with session_factory.begin() as session:
            await delete_owned_knowledge_bases(session, [user.id])
            await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == user.id))
            await session.execute(delete(User).where(User.id == user.id))

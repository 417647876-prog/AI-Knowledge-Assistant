import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import Settings
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
from app.jobs.contracts import HANDLER_FINALIZED
from app.jobs.repository import claim_next_job, complete_job, enqueue_job
from app.lifecycle.service import purge_document, purge_knowledge_base
from app.worker import main as worker_main
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

        result = await purge_document(
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
        assert result.completion_mode == HANDLER_FINALIZED
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
async def test_purge_recovers_after_file_deleted_but_database_commit_fails(tmp_path) -> None:
    user = User(
        id=uuid4(),
        username=f"purge_commit_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    stored_name = f"{uuid4()}.txt"
    stored_file = tmp_path / stored_name
    stored_file.write_text("private body", encoding="utf-8")

    class FailCommitSession:
        def __init__(self):
            self.inner = session_factory()

        async def __aenter__(self):
            await self.inner.__aenter__()
            return self

        async def __aexit__(self, *args):
            return await self.inner.__aexit__(*args)

        async def commit(self) -> None:
            await self.inner.flush()
            raise SQLAlchemyError("模拟数据库提交前退出")

        def __getattr__(self, name):
            return getattr(self.inner, name)

    class FailCommitFactory:
        def __call__(self):
            return FailCommitSession()

    try:
        async with session_factory.begin() as session:
            session.add(user)
            await session.flush()
            knowledge_base = KnowledgeBase(name="recover purge", owner_id=user.id)
            session.add(knowledge_base)
            await session.flush()
            deleted_at = datetime.now(UTC) - timedelta(days=8)
            document = Document(
                knowledge_base_id=knowledge_base.id,
                uploaded_by_user_id=user.id,
                original_file_name="recover.txt",
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
                worker_id="purge-commit-recovery",
                now=datetime.now(UTC),
                lease_seconds=120,
            )
        assert lease is not None and lease.job_id == job.id

        with pytest.raises(SQLAlchemyError):
            await purge_document(
                session_factory=FailCommitFactory(),
                upload_directory=tmp_path,
                lease=lease,
            )

        assert not stored_file.exists()
        async with session_factory() as session:
            persisted_job = await session.get(DocumentJob, job.id)
            persisted_document = await session.get(Document, document.id)
        assert persisted_job is not None and persisted_job.status == "processing"
        assert persisted_document is not None

        recovered = await purge_document(
            session_factory=session_factory,
            upload_directory=tmp_path,
            lease=lease,
        )

        assert recovered.completion_mode == HANDLER_FINALIZED
        async with session_factory() as session:
            assert await session.get(DocumentJob, job.id) is None
            assert await session.get(Document, document.id) is None
    finally:
        async with session_factory.begin() as session:
            await delete_owned_knowledge_bases(session, [user.id])
            await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == user.id))
            await session.execute(delete(User).where(User.id == user.id))


@pytest.mark.asyncio
async def test_purge_worker_cannot_use_shortened_retention_deadline(tmp_path) -> None:
    user = User(
        id=uuid4(),
        username=f"purge_retention_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    stored_name = f"{uuid4()}.txt"
    stored_file = tmp_path / stored_name
    stored_file.write_text("must survive retention", encoding="utf-8")
    try:
        async with session_factory.begin() as session:
            session.add(user)
            await session.flush()
            knowledge_base = KnowledgeBase(name="retention guard", owner_id=user.id)
            session.add(knowledge_base)
            await session.flush()
            document = Document(
                knowledge_base_id=knowledge_base.id,
                uploaded_by_user_id=user.id,
                original_file_name="retained.txt",
                stored_file_name=stored_name,
                content_type="text/plain",
                file_extension=".txt",
                file_size=22,
                file_hash=uuid4().hex * 2,
                status="ready",
                deleted_at=datetime.now(UTC) - timedelta(days=1),
                purge_after=datetime.now(UTC) - timedelta(seconds=1),
            )
            session.add(document)
            await session.flush()
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
                worker_id="purge-retention-test",
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

        assert captured.value.code == "PURGE_RETENTION_ACTIVE"
        assert stored_file.exists()
        async with session_factory() as session:
            persisted_document = await session.get(Document, document.id)
            persisted_job = await session.get(DocumentJob, job.id)
            denial = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.resource_id == document.id,
                    AuditEvent.action == "document.purge",
                    AuditEvent.result == "denied",
                )
            )
        assert persisted_document is not None
        assert persisted_job is not None and persisted_job.status == "processing"
        assert denial is not None
        assert denial.security_summary == {"reason": "PURGE_RETENTION_ACTIVE"}
    finally:
        async with session_factory.begin() as session:
            await delete_owned_knowledge_bases(session, [user.id])
            await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == user.id))
            await session.execute(delete(User).where(User.id == user.id))


@pytest.mark.asyncio
async def test_worker_completion_database_error_keeps_job_for_recovery(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = User(
        id=uuid4(),
        username=f"completion_{uuid4().hex[:20]}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    try:
        async with session_factory.begin() as session:
            session.add(user)
            await session.flush()
            knowledge_base = KnowledgeBase(name="completion recovery", owner_id=user.id)
            session.add(knowledge_base)
            await session.flush()
            document = Document(
                knowledge_base_id=knowledge_base.id,
                uploaded_by_user_id=user.id,
                original_file_name="completion.txt",
                stored_file_name=f"{uuid4()}.txt",
                content_type="text/plain",
                file_extension=".txt",
                file_size=1,
                file_hash=uuid4().hex * 2,
                status="ready",
            )
            session.add(document)
            await session.flush()
            job = await enqueue_job(
                session,
                job_type="ingest_document",
                resource_type="document",
                resource_id=document.id,
                owner_user_id=user.id,
                knowledge_base_id=knowledge_base.id,
            )

        async def fail_after_status_update(session, **kwargs):
            await complete_job(session, **kwargs)
            raise SQLAlchemyError("模拟 completion 提交前失败")

        monkeypatch.setattr(worker_main, "complete_job", fail_after_status_update)
        monkeypatch.setattr(worker_main, "record_worker_heartbeat", AsyncMock())

        with pytest.raises(SQLAlchemyError):
            await worker_main.run_worker_iteration(
                session_factory=session_factory,
                settings=Settings(_env_file=None),
                worker_id="completion-recovery-test",
                process_job=AsyncMock(return_value=1),
            )

        async with session_factory() as session:
            persisted_job = await session.get(DocumentJob, job.id)
        assert persisted_job is not None
        assert persisted_job.status == "processing"
        assert persisted_job.lease_token is not None
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


@pytest.mark.parametrize(
    ("job_type", "dangling"),
    [("purge_document", False), ("purge_knowledge_base", True)],
)
@pytest.mark.asyncio
async def test_purge_rejects_root_internal_symlink_without_touching_any_resource(
    tmp_path, job_type: str, dangling: bool
) -> None:
    owner = User(
        id=uuid4(),
        username=f"symlink_owner_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    other_owner = User(
        id=uuid4(),
        username=f"symlink_target_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    target_name = f"{uuid4()}.txt"
    target_file = tmp_path / target_name
    if not dangling:
        target_file.write_text("another owner's active content", encoding="utf-8")
    link_name = f"{uuid4()}.txt"
    link_file = tmp_path / link_name
    try:
        link_file.symlink_to(target_file)
    except OSError:
        pytest.skip("当前 Windows 环境不允许创建符号链接")
    try:
        async with session_factory.begin() as session:
            session.add_all([owner, other_owner])
            await session.flush()
            target_kb = KnowledgeBase(name="active target", owner_id=other_owner.id)
            victim_deleted_at = datetime.now(UTC) - timedelta(days=8)
            victim_kb = KnowledgeBase(
                name="deleted victim",
                owner_id=owner.id,
                deleted_at=victim_deleted_at,
                purge_after=victim_deleted_at + timedelta(days=7),
            )
            session.add_all([target_kb, victim_kb])
            await session.flush()
            target_document = Document(
                knowledge_base_id=target_kb.id,
                uploaded_by_user_id=other_owner.id,
                original_file_name="active.txt",
                stored_file_name=target_name,
                content_type="text/plain",
                file_extension=".txt",
                file_size=12,
                file_hash=uuid4().hex * 2,
                status="ready",
            )
            victim_document = Document(
                knowledge_base_id=victim_kb.id,
                uploaded_by_user_id=owner.id,
                original_file_name="deleted.txt",
                stored_file_name=link_name,
                content_type="text/plain",
                file_extension=".txt",
                file_size=12,
                file_hash=uuid4().hex * 2,
                status="ready",
                deleted_at=victim_deleted_at,
                purge_after=victim_deleted_at + timedelta(days=7),
            )
            session.add_all([target_document, victim_document])
            await session.flush()
            from app.jobs.repository import enqueue_job

            resource_type = "document" if job_type == "purge_document" else "knowledge_base"
            resource_id = victim_document.id if resource_type == "document" else victim_kb.id
            job = await enqueue_job(
                session,
                job_type=job_type,
                resource_type=resource_type,
                resource_id=resource_id,
                owner_user_id=owner.id,
                knowledge_base_id=victim_kb.id,
            )
        async with session_factory.begin() as session:
            lease = await claim_next_job(
                session,
                worker_id=f"symlink-{job_type}",
                now=datetime.now(UTC),
                lease_seconds=120,
            )
        assert lease is not None and lease.job_id == job.id

        handler = purge_document if job_type == "purge_document" else purge_knowledge_base
        with pytest.raises(AppError) as captured:
            await handler(
                session_factory=session_factory,
                upload_directory=tmp_path,
                lease=lease,
            )

        assert captured.value.code == "PURGE_PATH_INVALID"
        assert link_file.is_symlink()
        if not dangling:
            assert target_file.read_text(encoding="utf-8") == ("another owner's active content")
        async with session_factory() as session:
            assert await session.get(Document, target_document.id) is not None
            assert await session.get(Document, victim_document.id) is not None
            assert await session.get(KnowledgeBase, victim_kb.id) is not None
            denied = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.resource_id == resource_id,
                    AuditEvent.result == "denied",
                )
            )
        assert denied is not None
        assert denied.security_summary == {"reason": "PURGE_PATH_INVALID"}
    finally:
        link_file.unlink(missing_ok=True)
        target_file.unlink(missing_ok=True)
        async with session_factory.begin() as session:
            await delete_owned_knowledge_bases(session, [owner.id, other_owner.id])
            await session.execute(
                delete(AuditEvent).where(AuditEvent.actor_user_id.in_([owner.id, other_owner.id]))
            )
            await session.execute(delete(User).where(User.id.in_([owner.id, other_owner.id])))

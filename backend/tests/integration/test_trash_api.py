import asyncio
import hashlib
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db.models import (
    ADMIN_ROLE,
    USER_ROLE,
    AuditEvent,
    Document,
    DocumentChunk,
    DocumentJob,
    KnowledgeBase,
    User,
)
from app.db.session import session_factory
from app.jobs.repository import LeaseLostError, complete_job, fail_job
from app.main import create_app
from tests.database_cleanup import delete_owned_knowledge_bases

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@dataclass
class TrashContext:
    user: User
    client: httpx.AsyncClient


@pytest.fixture
async def trash_context() -> AsyncIterator[TrashContext]:
    user = User(
        id=uuid4(),
        username=f"trash_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    async with session_factory.begin() as session:
        session.add(user)
    token = create_access_token(user_id=user.id, role=user.role, settings=get_settings())
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()),
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        try:
            yield TrashContext(user=user, client=client)
        finally:
            async with session_factory.begin() as session:
                await delete_owned_knowledge_bases(session, [user.id])
                await session.execute(delete(AuditEvent).where(AuditEvent.actor_user_id == user.id))
                await session.execute(delete(User).where(User.id == user.id))


async def _seed_document(owner_id, tmp_path, *, active_job: bool = False):
    stored_name = f"{uuid4()}.txt"
    (tmp_path / stored_name).write_text("相同内容", encoding="utf-8")
    async with session_factory.begin() as session:
        knowledge_base = KnowledgeBase(name=f"回收站-{uuid4()}", owner_id=owner_id)
        session.add(knowledge_base)
        await session.flush()
        document = Document(
            knowledge_base_id=knowledge_base.id,
            uploaded_by_user_id=owner_id,
            original_file_name="制度.txt",
            stored_file_name=stored_name,
            content_type="text/plain",
            file_extension=".txt",
            file_size=12,
            file_hash=hashlib.sha256("相同内容".encode()).hexdigest(),
            status="ready",
        )
        session.add(document)
        await session.flush()
        job = DocumentJob(
            job_type="ingest_document",
            resource_type="document",
            resource_id=document.id,
            owner_user_id=owner_id,
            knowledge_base_id=knowledge_base.id,
            status="processing" if active_job else "succeeded",
            lease_token=uuid4() if active_job else None,
            lease_expires_at=(datetime.now(UTC) + timedelta(minutes=5)) if active_job else None,
        )
        session.add(job)
        session.add(
            DocumentChunk(
                document_id=document.id,
                knowledge_base_id=knowledge_base.id,
                chunk_index=0,
                content="正文不应出现在回收站响应",
                content_hash="8" * 64,
                embedding=[0.0] * 512,
            )
        )
    return knowledge_base, document, job


@pytest.mark.asyncio
async def test_document_soft_delete_is_idempotent_hidden_and_restorable(
    tmp_path, trash_context: TrashContext
) -> None:
    knowledge_base, document, _job = await _seed_document(trash_context.user.id, tmp_path)
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    settings.upload_directory = tmp_path
    try:
        first = await trash_context.client.delete(f"/api/v1/documents/{document.id}")
        second = await trash_context.client.delete(f"/api/v1/documents/{document.id}")
        hidden = await trash_context.client.get(f"/api/v1/documents/{document.id}")
        listed = await trash_context.client.get(
            f"/api/v1/knowledge-bases/{knowledge_base.id}/documents"
        )
        trash = await trash_context.client.get("/api/v1/trash")
    finally:
        settings.upload_directory = previous_upload_directory

    assert first.status_code == second.status_code == 204
    assert hidden.status_code == 404
    assert listed.json() == {"items": []}
    assert str(document.id) in trash.text
    assert "正文不应出现在回收站响应" not in trash.text
    async with session_factory() as session:
        deleted = await session.get(Document, document.id)
        assert deleted is not None
        assert deleted.deleted_at is not None
        assert deleted.purge_after == deleted.deleted_at + timedelta(days=7)
        first_deleted_at = deleted.deleted_at
    assert (tmp_path / document.stored_file_name).exists()

    restored = await trash_context.client.post(f"/api/v1/documents/{document.id}/restore")
    assert restored.status_code == 204
    async with session_factory() as session:
        restored_document = await session.get(Document, document.id)
        assert restored_document is not None and restored_document.deleted_at is None
        assert restored_document.purge_after is None
        events = (
            await session.scalars(select(AuditEvent).where(AuditEvent.resource_id == document.id))
        ).all()
    assert first_deleted_at is not None
    assert {event.action for event in events} >= {"document.delete", "document.restore"}


@pytest.mark.asyncio
async def test_soft_delete_cancels_an_active_job_instead_of_rejecting(
    tmp_path, trash_context: TrashContext
) -> None:
    _knowledge_base, document, job = await _seed_document(
        trash_context.user.id, tmp_path, active_job=True
    )
    old_token = job.lease_token
    assert old_token is not None

    response = await trash_context.client.delete(f"/api/v1/documents/{document.id}")

    assert response.status_code == 204
    async with session_factory.begin() as session:
        deleted = await session.get(Document, document.id)
        reappeared = await session.get(DocumentJob, job.id)
        assert deleted is not None and reappeared is not None
        first_deleted_at = deleted.deleted_at
        reappeared.status = "processing"
        old_token = uuid4()
        reappeared.lease_token = old_token
        reappeared.lease_expires_at = datetime.now(UTC) + timedelta(minutes=5)
    repeated = await trash_context.client.delete(f"/api/v1/documents/{document.id}")

    assert repeated.status_code == 204
    async with session_factory() as session:
        canceled = await session.get(DocumentJob, job.id)
        still_deleted = await session.get(Document, document.id)
        completed = await complete_job(
            session,
            job_id=job.id,
            lease_token=old_token,
            chunk_count=99,
            now=datetime.now(UTC),
        )
        with pytest.raises(LeaseLostError):
            await fail_job(
                session,
                job_id=job.id,
                lease_token=old_token,
                code="MODEL_TIMEOUT",
                message="late",
                retryable=True,
                now=datetime.now(UTC),
            )
    assert canceled is not None and canceled.status == "canceled"
    assert canceled.lease_token is None and canceled.lease_expires_at is None
    assert still_deleted is not None and still_deleted.deleted_at == first_deleted_at
    assert completed is False


@pytest.mark.asyncio
async def test_upload_same_hash_while_document_is_in_trash_is_rejected(
    tmp_path, trash_context: TrashContext
) -> None:
    knowledge_base, document, _job = await _seed_document(trash_context.user.id, tmp_path)
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    settings.upload_directory = tmp_path
    try:
        await trash_context.client.delete(f"/api/v1/documents/{document.id}")
        response = await trash_context.client.post(
            f"/api/v1/knowledge-bases/{knowledge_base.id}/documents",
            files={"file": ("再次上传.txt", "相同内容", "text/plain")},
        )
    finally:
        settings.upload_directory = previous_upload_directory

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DOCUMENT_IN_TRASH"


@pytest.mark.asyncio
async def test_other_owner_and_admin_cannot_see_or_mutate_trash_resources(
    tmp_path, trash_context: TrashContext
) -> None:
    knowledge_base, document, _job = await _seed_document(trash_context.user.id, tmp_path)
    assert (
        await trash_context.client.delete(f"/api/v1/documents/{document.id}")
    ).status_code == 204
    attackers = [
        User(
            id=uuid4(),
            username=f"trash_{role}_{uuid4().hex[:20]}",
            password_hash=hash_password("correct horse battery"),
            role=role,
            is_active=True,
        )
        for role in (USER_ROLE, ADMIN_ROLE)
    ]
    async with session_factory.begin() as session:
        session.add_all(attackers)

    try:
        for attacker in attackers:
            token = create_access_token(
                user_id=attacker.id,
                role=attacker.role,
                settings=get_settings(),
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app()),
                base_url="http://test",
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                trash = await client.get("/api/v1/trash")
                assert trash.status_code == 200
                assert str(document.id) not in trash.text
                assert str(knowledge_base.id) not in trash.text

                operations = [
                    ("DELETE", f"/api/v1/documents/{document.id}", "DOCUMENT_NOT_FOUND"),
                    (
                        "POST",
                        f"/api/v1/documents/{document.id}/restore",
                        "DOCUMENT_NOT_FOUND",
                    ),
                    (
                        "DELETE",
                        f"/api/v1/documents/{document.id}/purge",
                        "DOCUMENT_NOT_FOUND",
                    ),
                    (
                        "DELETE",
                        f"/api/v1/knowledge-bases/{knowledge_base.id}",
                        "KNOWLEDGE_BASE_NOT_FOUND",
                    ),
                    (
                        "POST",
                        f"/api/v1/knowledge-bases/{knowledge_base.id}/restore",
                        "KNOWLEDGE_BASE_NOT_FOUND",
                    ),
                    (
                        "DELETE",
                        f"/api/v1/knowledge-bases/{knowledge_base.id}/purge",
                        "KNOWLEDGE_BASE_NOT_FOUND",
                    ),
                ]
                for method, path, error_code in operations:
                    response = await client.request(method, path)
                    assert response.status_code == 404
                    assert response.json()["error"]["code"] == error_code
    finally:
        attacker_ids = [attacker.id for attacker in attackers]
        async with session_factory.begin() as session:
            await session.execute(
                delete(AuditEvent).where(AuditEvent.actor_user_id.in_(attacker_ids))
            )
            await session.execute(delete(User).where(User.id.in_(attacker_ids)))


@pytest.mark.asyncio
async def test_upload_same_hash_after_tombstone_retention_expired_is_allowed(
    tmp_path, trash_context: TrashContext
) -> None:
    knowledge_base, document, _job = await _seed_document(trash_context.user.id, tmp_path)
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    settings.upload_directory = tmp_path
    try:
        await trash_context.client.delete(f"/api/v1/documents/{document.id}")
        async with session_factory.begin() as session:
            expired = await session.get(Document, document.id, with_for_update=True)
            assert expired is not None
            expired.deleted_at = datetime.now(UTC) - timedelta(days=8)
            expired.purge_after = datetime.now(UTC) - timedelta(days=1)
        response = await trash_context.client.post(
            f"/api/v1/knowledge-bases/{knowledge_base.id}/documents",
            files={"file": ("重新上传.txt", "相同内容", "text/plain")},
        )
    finally:
        settings.upload_directory = previous_upload_directory

    assert response.status_code == 202
    assert response.json()["document_id"] != str(document.id)


@pytest.mark.asyncio
async def test_maintenance_cannot_shorten_document_purge_retention(
    tmp_path, trash_context: TrashContext
) -> None:
    knowledge_base, document, _job = await _seed_document(trash_context.user.id, tmp_path)
    await trash_context.client.delete(f"/api/v1/documents/{document.id}")
    async with session_factory.begin() as session:
        shortened = await session.get(Document, document.id, with_for_update=True)
        assert shortened is not None
        shortened.purge_after = datetime.now(UTC) - timedelta(seconds=1)

    trash = await trash_context.client.get("/api/v1/trash")
    assert trash.status_code == 200
    trash_document = next(
        item for item in trash.json()["documents"] if item["id"] == str(document.id)
    )
    assert datetime.fromisoformat(trash_document["purge_after"]) > datetime.now(UTC)

    duplicate = await trash_context.client.post(
        f"/api/v1/knowledge-bases/{knowledge_base.id}/documents",
        files={"file": ("重新上传.txt", "相同内容", "text/plain")},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "DOCUMENT_IN_TRASH"

    purge = await trash_context.client.delete(f"/api/v1/documents/{document.id}/purge")
    assert purge.status_code == 409
    assert purge.json()["error"]["code"] == "PURGE_RETENTION_ACTIVE"

    restored = await trash_context.client.post(f"/api/v1/documents/{document.id}/restore")
    assert restored.status_code == 204


@pytest.mark.asyncio
async def test_restore_document_conflict_does_not_modify_deleted_document(
    tmp_path, trash_context: TrashContext
) -> None:
    knowledge_base, document, _job = await _seed_document(trash_context.user.id, tmp_path)
    await trash_context.client.delete(f"/api/v1/documents/{document.id}")
    async with session_factory.begin() as session:
        session.add(
            Document(
                knowledge_base_id=knowledge_base.id,
                uploaded_by_user_id=trash_context.user.id,
                original_file_name="新文档.txt",
                stored_file_name=f"{uuid4()}.txt",
                content_type="text/plain",
                file_extension=".txt",
                file_size=12,
                file_hash=document.file_hash,
                status="ready",
            )
        )

    response = await trash_context.client.post(f"/api/v1/documents/{document.id}/restore")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DOCUMENT_RESTORE_CONFLICT"
    async with session_factory() as session:
        still_deleted = await session.get(Document, document.id)
    assert still_deleted is not None and still_deleted.deleted_at is not None


@pytest.mark.asyncio
async def test_knowledge_base_delete_and_restore_coordinates_child_state(
    tmp_path, trash_context: TrashContext
) -> None:
    knowledge_base, document, job = await _seed_document(
        trash_context.user.id, tmp_path, active_job=True
    )

    deleted = await trash_context.client.delete(f"/api/v1/knowledge-bases/{knowledge_base.id}")

    assert deleted.status_code == 204
    async with session_factory() as session:
        deleted_kb = await session.get(KnowledgeBase, knowledge_base.id)
        deleted_document = await session.get(Document, document.id)
        canceled_job = await session.get(DocumentJob, job.id)
        assert deleted_kb is not None and deleted_document is not None
        assert deleted_kb.deleted_at == deleted_document.deleted_at
        assert deleted_kb.purge_after == deleted_document.purge_after
        assert canceled_job is not None and canceled_job.status == "canceled"

    restored = await trash_context.client.post(
        f"/api/v1/knowledge-bases/{knowledge_base.id}/restore"
    )
    assert restored.status_code == 204
    async with session_factory() as session:
        restored_kb = await session.get(KnowledgeBase, knowledge_base.id)
        restored_document = await session.get(Document, document.id)
    assert restored_kb is not None and restored_kb.deleted_at is None
    assert restored_document is not None and restored_document.deleted_at is None


@pytest.mark.asyncio
async def test_maintenance_cannot_shorten_knowledge_base_or_child_retention(
    tmp_path, trash_context: TrashContext
) -> None:
    knowledge_base, document, _job = await _seed_document(trash_context.user.id, tmp_path)
    assert (
        await trash_context.client.delete(f"/api/v1/knowledge-bases/{knowledge_base.id}")
    ).status_code == 204
    async with session_factory.begin() as session:
        shortened_kb = await session.get(KnowledgeBase, knowledge_base.id, with_for_update=True)
        shortened_document = await session.get(Document, document.id, with_for_update=True)
        assert shortened_kb is not None and shortened_document is not None
        shortened_kb.purge_after = datetime.now(UTC) - timedelta(seconds=1)
        shortened_document.purge_after = datetime.now(UTC) - timedelta(seconds=1)

    trash = await trash_context.client.get("/api/v1/trash")
    assert trash.status_code == 200
    trash_kb = next(
        item for item in trash.json()["knowledge_bases"] if item["id"] == str(knowledge_base.id)
    )
    trash_document = next(
        item for item in trash.json()["documents"] if item["id"] == str(document.id)
    )
    assert datetime.fromisoformat(trash_kb["purge_after"]) > datetime.now(UTC)
    assert trash_kb["purge_after"] == trash_document["purge_after"]

    purge = await trash_context.client.delete(f"/api/v1/knowledge-bases/{knowledge_base.id}/purge")
    assert purge.status_code == 409
    assert purge.json()["error"]["code"] == "PURGE_RETENTION_ACTIVE"

    restored = await trash_context.client.post(
        f"/api/v1/knowledge-bases/{knowledge_base.id}/restore"
    )
    assert restored.status_code == 204
    async with session_factory() as session:
        restored_kb = await session.get(KnowledgeBase, knowledge_base.id)
        restored_document = await session.get(Document, document.id)
    assert restored_kb is not None and restored_kb.deleted_at is None
    assert restored_document is not None and restored_document.deleted_at is None


@pytest.mark.asyncio
async def test_restore_expires_and_purge_request_is_idempotent_only_after_deadline(
    tmp_path, trash_context: TrashContext
) -> None:
    _knowledge_base, document, _job = await _seed_document(trash_context.user.id, tmp_path)
    assert (
        await trash_context.client.delete(f"/api/v1/documents/{document.id}")
    ).status_code == 204

    too_early = await trash_context.client.delete(f"/api/v1/documents/{document.id}/purge")
    assert too_early.status_code == 409
    assert too_early.json()["error"]["code"] == "PURGE_RETENTION_ACTIVE"

    async with session_factory.begin() as session:
        persisted = await session.get(Document, document.id, with_for_update=True)
        assert persisted is not None
        persisted.deleted_at = datetime.now(UTC) - timedelta(days=8)
        persisted.purge_after = datetime.now(UTC) - timedelta(seconds=1)

    expired_restore, first, second = await asyncio.gather(
        trash_context.client.post(f"/api/v1/documents/{document.id}/restore"),
        trash_context.client.delete(f"/api/v1/documents/{document.id}/purge"),
        trash_context.client.delete(f"/api/v1/documents/{document.id}/purge"),
    )
    trash = await trash_context.client.get("/api/v1/trash")

    assert expired_restore.status_code == 409
    assert expired_restore.json()["error"]["code"] == "DOCUMENT_RETENTION_EXPIRED"
    assert first.status_code == second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert str(document.id) not in trash.text


@pytest.mark.asyncio
async def test_concurrent_same_hash_restores_have_one_stable_conflict(
    tmp_path, trash_context: TrashContext
) -> None:
    knowledge_base, first_document, _job = await _seed_document(trash_context.user.id, tmp_path)
    await trash_context.client.delete(f"/api/v1/documents/{first_document.id}")
    async with session_factory.begin() as session:
        first = await session.get(Document, first_document.id)
        assert first is not None
        second_document = Document(
            knowledge_base_id=knowledge_base.id,
            uploaded_by_user_id=trash_context.user.id,
            original_file_name="另一个回收站文档.txt",
            stored_file_name=f"{uuid4()}.txt",
            content_type="text/plain",
            file_extension=".txt",
            file_size=12,
            file_hash=first.file_hash,
            status="ready",
            deleted_at=first.deleted_at,
            purge_after=first.purge_after,
        )
        session.add(second_document)
        await session.flush()
        second_id = second_document.id

    responses = await asyncio.gather(
        trash_context.client.post(f"/api/v1/documents/{first_document.id}/restore"),
        trash_context.client.post(f"/api/v1/documents/{second_id}/restore"),
    )

    assert sorted(response.status_code for response in responses) == [204, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "DOCUMENT_RESTORE_CONFLICT"
    async with session_factory() as session:
        documents = (
            await session.scalars(
                select(Document).where(Document.id.in_([first_document.id, second_id]))
            )
        ).all()
    assert sum(document.deleted_at is None for document in documents) == 1

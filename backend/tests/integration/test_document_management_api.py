import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from app.ai.embeddings import FakeEmbeddingProvider
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db.models import USER_ROLE, DocumentChunk, DocumentJob, KnowledgeBase, User
from app.db.models.document import Document
from app.db.session import session_factory
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
class DocumentManagementContext:
    user: User
    client: httpx.AsyncClient


@pytest.fixture
async def document_management_context() -> AsyncIterator[DocumentManagementContext]:
    user = User(
        id=uuid4(),
        username=f"doc_mgmt_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    async with session_factory.begin() as session:
        session.add(user)
    token = create_access_token(user_id=user.id, role=user.role, settings=get_settings())
    transport = httpx.ASGITransport(app=create_app())
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        try:
            yield DocumentManagementContext(user=user, client=client)
        finally:
            async with session_factory.begin() as session:
                await delete_owned_knowledge_bases(session, [user.id])
                await session.execute(delete(User).where(User.id == user.id))


async def _create_document(
    tmp_path,
    owner_id,
    *,
    file_name: str = "员工手册.txt",
    reverse_job_ids: bool = False,
    active_job: bool = False,
    embedding: list[float] | None = None,
) -> tuple[KnowledgeBase, Document, list[DocumentJob]]:
    stored_file_name = f"{uuid4()}.txt"
    (tmp_path / stored_file_name).write_text("员工入职满一年享受五天年假。", encoding="utf-8")
    async with session_factory() as session:
        knowledge_base = KnowledgeBase(name=f"文档管理测试-{uuid4()}", owner_id=owner_id)
        session.add(knowledge_base)
        await session.flush()
        document = Document(
            knowledge_base_id=knowledge_base.id,
            uploaded_by_user_id=owner_id,
            original_file_name=file_name,
            stored_file_name=stored_file_name,
            content_type="text/plain",
            file_extension=".txt",
            file_size=42,
            file_hash=uuid4().hex * 2,
            status="failed",
            error_code="PARSE_FAILED",
            error_message="无法解析文档。",
        )
        session.add(document)
        await session.flush()
        older_id = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff") if reverse_job_ids else uuid4()
        newer_id = UUID("00000000-0000-0000-0000-000000000001") if reverse_job_ids else uuid4()
        jobs = [
            DocumentJob(
                id=older_id,
                job_type="ingest_document",
                resource_type="document",
                resource_id=document.id,
                owner_user_id=owner_id,
                knowledge_base_id=knowledge_base.id,
                status="failed",
                stage="parse",
                created_at=datetime(2026, 7, 14, 8, 0, tzinfo=UTC),
            ),
            DocumentJob(
                id=newer_id,
                job_type="ingest_document",
                resource_type="document",
                resource_id=document.id,
                owner_user_id=owner_id,
                knowledge_base_id=knowledge_base.id,
                status="processing" if active_job else "succeeded",
                stage="store",
                created_at=datetime(2026, 7, 14, 9, 0, tzinfo=UTC),
            ),
        ]
        session.add_all(jobs)
        session.add(
            DocumentChunk(
                document_id=document.id,
                knowledge_base_id=knowledge_base.id,
                chunk_index=0,
                content="员工入职满一年享受五天年假。",
                content_hash=uuid4().hex * 2,
                extra_metadata={},
                embedding=embedding or [0.0] * 512,
            )
        )
        await session.commit()
        return knowledge_base, document, jobs


@pytest.mark.asyncio
async def test_list_documents_returns_persisted_documents_with_latest_job(
    tmp_path, document_management_context: DocumentManagementContext
) -> None:
    knowledge_base, document, jobs = await _create_document(
        tmp_path, document_management_context.user.id, reverse_job_ids=True
    )
    await _create_document(tmp_path, document_management_context.user.id, file_name="另一个库.txt")

    response = await document_management_context.client.get(
        f"/api/v1/knowledge-bases/{knowledge_base.id}/documents"
    )

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "document_id": str(document.id),
                "job_id": str(jobs[1].id),
                "file_name": "员工手册.txt",
                "status": "failed",
                "error_code": "PARSE_FAILED",
                "error_message": "无法解析文档。",
            }
        ]
    }


@pytest.mark.asyncio
async def test_get_document_uses_latest_job_created_at(
    tmp_path, document_management_context: DocumentManagementContext
) -> None:
    _knowledge_base, document, jobs = await _create_document(
        tmp_path,
        document_management_context.user.id,
        reverse_job_ids=True,
    )

    response = await document_management_context.client.get(f"/api/v1/documents/{document.id}")

    assert response.status_code == 200
    assert response.json()["job_id"] == str(jobs[1].id)


@pytest.mark.asyncio
async def test_delete_document_soft_deletes_and_keeps_records_and_original_file(
    tmp_path, document_management_context: DocumentManagementContext
) -> None:
    _knowledge_base, document, jobs = await _create_document(
        tmp_path, document_management_context.user.id
    )
    stored_file = tmp_path / document.stored_file_name
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    settings.upload_directory = tmp_path
    try:
        response = await document_management_context.client.delete(
            f"/api/v1/documents/{document.id}"
        )
        repeated_response = await document_management_context.client.delete(
            f"/api/v1/documents/{document.id}"
        )
    finally:
        settings.upload_directory = previous_upload_directory

    assert response.status_code == 204
    assert repeated_response.status_code == 204
    assert stored_file.exists()
    async with session_factory() as session:
        deleted_document = await session.get(Document, document.id)
        assert deleted_document is not None and deleted_document.deleted_at is not None
        assert (
            await session.scalar(
                select(DocumentChunk.id).where(DocumentChunk.document_id == document.id)
            )
        ) is not None
        assert (
            await session.scalar(
                select(DocumentJob.id).where(
                    DocumentJob.job_type == "ingest_document",
                    DocumentJob.resource_type == "document",
                    DocumentJob.resource_id == document.id,
                )
            )
        ) is not None
    assert len(jobs) == 2


@pytest.mark.asyncio
async def test_delete_document_cancels_active_ingestion_job(
    tmp_path, document_management_context: DocumentManagementContext
) -> None:
    _knowledge_base, document, _jobs = await _create_document(
        tmp_path,
        document_management_context.user.id,
        active_job=True,
    )
    stored_file = tmp_path / document.stored_file_name
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    settings.upload_directory = tmp_path
    try:
        response = await document_management_context.client.delete(
            f"/api/v1/documents/{document.id}"
        )
    finally:
        settings.upload_directory = previous_upload_directory

    assert response.status_code == 204
    assert stored_file.exists()
    async with session_factory() as session:
        deleted_document = await session.get(Document, document.id)
        canceled_job = await session.scalar(
            select(DocumentJob).where(
                DocumentJob.resource_type == "document",
                DocumentJob.resource_id == document.id,
                DocumentJob.status == "canceled",
            )
        )
    assert deleted_document is not None and deleted_document.deleted_at is not None
    assert canceled_job is not None


@pytest.mark.asyncio
async def test_soft_delete_does_not_touch_the_original_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    document_management_context: DocumentManagementContext,
) -> None:
    _knowledge_base, document, _jobs = await _create_document(
        tmp_path, document_management_context.user.id
    )
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    settings.upload_directory = tmp_path

    def fail_unlink(_path: Path, *, missing_ok: bool = False) -> None:
        raise OSError("模拟文件占用")

    monkeypatch.setattr(Path, "unlink", fail_unlink)
    try:
        response = await document_management_context.client.delete(
            f"/api/v1/documents/{document.id}"
        )
    finally:
        settings.upload_directory = previous_upload_directory

    assert response.status_code == 204
    async with session_factory() as session:
        deleted_document = await session.get(Document, document.id)
        assert deleted_document is not None and deleted_document.deleted_at is not None
        assert (
            await session.scalar(
                select(DocumentChunk.id).where(DocumentChunk.document_id == document.id)
            )
        ) is not None


@pytest.mark.asyncio
async def test_question_does_not_cite_deleted_document(
    tmp_path, document_management_context: DocumentManagementContext
) -> None:
    question = "年假有几天？"
    embedding = await FakeEmbeddingProvider(dimensions=512).embed_query(question)
    knowledge_base, document, _jobs = await _create_document(
        tmp_path,
        document_management_context.user.id,
        embedding=embedding,
    )
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    previous_embedding_provider = settings.embedding_provider
    previous_score_threshold = settings.rag_score_threshold
    settings.upload_directory = tmp_path
    settings.embedding_provider = "fake"
    settings.rag_score_threshold = -1.0
    try:
        before_delete = await document_management_context.client.post(
            f"/api/v1/knowledge-bases/{knowledge_base.id}/questions",
            json={"question": question},
        )
        delete_response = await document_management_context.client.delete(
            f"/api/v1/documents/{document.id}"
        )
        after_delete = await document_management_context.client.post(
            f"/api/v1/knowledge-bases/{knowledge_base.id}/questions",
            json={"question": question},
        )
    finally:
        settings.upload_directory = previous_upload_directory
        settings.embedding_provider = previous_embedding_provider
        settings.rag_score_threshold = previous_score_threshold

    assert before_delete.status_code == 200
    assert before_delete.json()["citations"][0]["document_id"] == str(document.id)
    assert delete_response.status_code == 204
    assert after_delete.status_code == 200
    assert after_delete.json()["citations"] == []
    assert after_delete.json()["retrieved_chunk_count"] == 0


@pytest.mark.asyncio
async def test_delete_missing_document_returns_safe_not_found(
    document_management_context: DocumentManagementContext,
) -> None:
    response = await document_management_context.client.delete(f"/api/v1/documents/{uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"

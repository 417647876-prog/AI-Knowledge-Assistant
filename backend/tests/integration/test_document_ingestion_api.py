import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.embeddings import FakeEmbeddingProvider
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token, hash_password
from app.db.models import USER_ROLE, Document, DocumentJob, KnowledgeBase, User
from app.db.session import session_factory
from app.knowledge import background
from app.main import create_app
from app.worker.main import run_worker_iteration

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@pytest.fixture
async def authenticated_client() -> AsyncIterator[httpx.AsyncClient]:
    user = User(
        id=uuid4(),
        username=f"doc_ingest_{uuid4().hex}",
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
            yield client
        finally:
            async with session_factory.begin() as session:
                await session.execute(
                    delete(KnowledgeBase).where(KnowledgeBase.owner_id == user.id)
                )
                await session.execute(delete(User).where(User.id == user.id))


async def _run_worker_once(settings, worker_id: str) -> bool:
    async def process(lease):
        return await background.process_ingest_document(lease, settings)

    return await run_worker_iteration(
        session_factory=session_factory,
        settings=settings,
        worker_id=worker_id,
        process_job=process,
    )


@pytest.mark.asyncio
async def test_upload_only_enqueues_then_worker_ingests_to_ready(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    authenticated_client: httpx.AsyncClient,
) -> None:
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    previous_embedding_provider = settings.embedding_provider
    previous_max_attempts = settings.job_max_attempts
    settings.upload_directory = tmp_path
    settings.embedding_provider = "fake"
    settings.job_max_attempts = 5
    embedding_calls = 0
    original_embed = FakeEmbeddingProvider.embed_documents

    async def count_embeddings(self, texts):
        nonlocal embedding_calls
        embedding_calls += 1
        return await original_embed(self, texts)

    monkeypatch.setattr(FakeEmbeddingProvider, "embed_documents", count_embeddings)
    try:
        knowledge_base = (
            await authenticated_client.post(
                "/api/v1/knowledge-bases",
                json={"name": f"1C API 测试 {uuid4()}"},
            )
        ).json()
        response = await authenticated_client.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("制度.txt", "第一条制度。第二条制度。", "text/plain")},
        )
        assert response.status_code == 202
        document_id = response.json()["document_id"]
        queued_state = await authenticated_client.get(f"/api/v1/documents/{document_id}")
        async with session_factory() as session:
            persisted_job = await session.scalar(
                select(DocumentJob).where(
                    DocumentJob.job_type == "ingest_document",
                    DocumentJob.resource_type == "document",
                    DocumentJob.resource_id == document_id,
                )
            )

        assert embedding_calls == 0
        assert queued_state.json()["status"] == "pending"
        assert persisted_job is not None and persisted_job.status == "pending"
        assert persisted_job.max_attempts == 5

        assert await _run_worker_once(settings, "ingestion-api-test-worker")
        state = await authenticated_client.get(f"/api/v1/documents/{document_id}")
    finally:
        settings.upload_directory = previous_upload_directory
        settings.embedding_provider = previous_embedding_provider
        settings.job_max_attempts = previous_max_attempts

    assert state.status_code == 200
    assert state.json()["status"] == "ready"
    assert embedding_calls == 1
    async with session_factory() as session:
        finished_job = await session.get(DocumentJob, persisted_job.id)
    assert finished_job is not None and finished_job.status == "succeeded"


@pytest.mark.asyncio
async def test_whitespace_document_finishes_failed_with_safe_error(
    tmp_path, authenticated_client: httpx.AsyncClient
) -> None:
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    previous_embedding_provider = settings.embedding_provider
    settings.upload_directory = tmp_path
    settings.embedding_provider = "fake"
    try:
        knowledge_base = (
            await authenticated_client.post(
                "/api/v1/knowledge-bases",
                json={"name": f"1C 空文档测试 {uuid4()}"},
            )
        ).json()
        response = await authenticated_client.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("空白.txt", "   \n  ", "text/plain")},
        )
        assert response.status_code == 202
        assert response.json()["status"] == "pending"
        assert await _run_worker_once(settings, "empty-document-test-worker")
        state = await authenticated_client.get(
            f"/api/v1/documents/{response.json()['document_id']}"
        )
    finally:
        settings.upload_directory = previous_upload_directory
        settings.embedding_provider = previous_embedding_provider

    assert state.json()["status"] == "failed"
    assert state.json()["error_code"] == "DOCUMENT_CONTENT_EMPTY"
    assert state.json()["error_message"] == "文档没有可入库的内容。"
    async with session_factory() as session:
        failed_document = await session.get(Document, response.json()["document_id"])
    assert failed_document is not None and failed_document.status == "failed"


@pytest.mark.asyncio
async def test_upload_enforces_exact_file_size_boundary(
    tmp_path, authenticated_client: httpx.AsyncClient
) -> None:
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    previous_max_upload_bytes = settings.max_upload_bytes
    settings.upload_directory = tmp_path
    settings.max_upload_bytes = 4
    try:
        knowledge_base = (
            await authenticated_client.post(
                "/api/v1/knowledge-bases",
                json={"name": f"1B 边界测试 {uuid4()}"},
            )
        ).json()
        accepted = await authenticated_client.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("four.txt", b"1234", "text/plain")},
        )
        rejected = await authenticated_client.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("five.txt", b"12345", "text/plain")},
        )
    finally:
        settings.upload_directory = previous_upload_directory
        settings.max_upload_bytes = previous_max_upload_bytes

    assert accepted.status_code == 202
    assert rejected.status_code == 413
    assert rejected.json()["error"]["code"] == "FILE_TOO_LARGE"


@pytest.mark.asyncio
async def test_database_failure_removes_new_upload_but_keeps_existing_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    authenticated_client: httpx.AsyncClient,
) -> None:
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    settings.upload_directory = tmp_path
    existing_file = tmp_path / "existing.txt"
    existing_file.write_text("已有文件", encoding="utf-8")
    try:
        knowledge_base = (
            await authenticated_client.post(
                "/api/v1/knowledge-bases",
                json={"name": f"提交失败测试 {uuid4()}"},
            )
        ).json()

        async def fail_commit(_session: AsyncSession) -> None:
            raise SQLAlchemyError("simulated commit failure")

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "commit", fail_commit)
            with pytest.raises(SQLAlchemyError, match="simulated commit failure"):
                await authenticated_client.post(
                    f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
                    files={"file": ("new.txt", "新文件", "text/plain")},
                )
    finally:
        settings.upload_directory = previous_upload_directory

    assert existing_file.exists()
    assert list(tmp_path.iterdir()) == [existing_file]
    async with session_factory() as session:
        persisted = await session.scalar(
            select(Document.id).where(Document.knowledge_base_id == knowledge_base["id"])
        )
    assert persisted is None


@pytest.mark.asyncio
async def test_file_write_failure_leaves_no_database_record_or_partial_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    authenticated_client: httpx.AsyncClient,
) -> None:
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    settings.upload_directory = tmp_path
    try:
        knowledge_base = (
            await authenticated_client.post(
                "/api/v1/knowledge-bases",
                json={"name": f"写入失败测试 {uuid4()}"},
            )
        ).json()

        def fail_write(path: Path, _content: bytes) -> int:
            path.touch()
            raise OSError("simulated disk failure")

        with monkeypatch.context() as patch:
            patch.setattr(Path, "write_bytes", fail_write)
            response = await authenticated_client.post(
                f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
                files={"file": ("new.txt", "新文件", "text/plain")},
            )
    finally:
        settings.upload_directory = previous_upload_directory

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "DOCUMENT_UPLOAD_FAILED"
    assert list(tmp_path.iterdir()) == []
    async with session_factory() as session:
        persisted = await session.scalar(
            select(Document.id).where(Document.knowledge_base_id == knowledge_base["id"])
        )
    assert persisted is None


@pytest.mark.asyncio
async def test_file_write_cleanup_failure_does_not_mask_stable_upload_error(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    authenticated_client: httpx.AsyncClient,
) -> None:
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    settings.upload_directory = tmp_path
    try:
        knowledge_base = (
            await authenticated_client.post(
                "/api/v1/knowledge-bases",
                json={"name": f"cleanup failure {uuid4()}"},
            )
        ).json()

        def fail_write(path: Path, _content: bytes) -> int:
            path.touch()
            raise OSError("simulated disk failure")

        def fail_unlink(_path: Path, *, missing_ok: bool = False) -> None:
            raise OSError("simulated cleanup failure")

        with monkeypatch.context() as patch:
            patch.setattr(Path, "write_bytes", fail_write)
            patch.setattr(Path, "unlink", fail_unlink)
            response = await authenticated_client.post(
                f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
                files={"file": ("new.txt", "new file", "text/plain")},
            )
    finally:
        settings.upload_directory = previous_upload_directory

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "DOCUMENT_UPLOAD_FAILED"


@pytest.mark.asyncio
async def test_transient_embedding_failure_retries_with_configured_backoff_then_exhausts(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    authenticated_client: httpx.AsyncClient,
) -> None:
    settings = get_settings()
    previous_directory = settings.upload_directory
    previous_provider = settings.embedding_provider
    previous_attempts = settings.job_max_attempts
    previous_backoff = settings.job_retry_backoff_seconds
    settings.upload_directory = tmp_path
    settings.embedding_provider = "fake"
    settings.job_max_attempts = 2
    settings.job_retry_backoff_seconds = (7, 19)

    async def timeout(_provider, _texts):
        raise AppError(code="MODEL_TIMEOUT", message="upstream timeout", status_code=502)

    monkeypatch.setattr(FakeEmbeddingProvider, "embed_documents", timeout)
    try:
        knowledge_base = (
            await authenticated_client.post(
                "/api/v1/knowledge-bases",
                json={"name": f"retry test {uuid4()}"},
            )
        ).json()
        response = await authenticated_client.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("retry.txt", "retry content", "text/plain")},
        )
        job_id = response.json()["job_id"]

        assert await _run_worker_once(settings, "retry-worker-1")
        async with session_factory() as session:
            waiting_job = await session.get(DocumentJob, job_id)
            assert waiting_job is not None
            assert waiting_job.status == "retry_wait"
            assert waiting_job.heartbeat_at is not None
            assert waiting_job.run_after == waiting_job.heartbeat_at + timedelta(seconds=7)
            await session.execute(
                update(DocumentJob)
                .where(DocumentJob.id == waiting_job.id)
                .values(run_after=datetime.now(UTC) - timedelta(seconds=1))
            )
            await session.commit()

        assert await _run_worker_once(settings, "retry-worker-2")
        async with session_factory() as session:
            failed_job = await session.get(DocumentJob, job_id)
            failed_document = await session.get(Document, response.json()["document_id"])
        assert failed_job is not None
        assert failed_job.status == "failed"
        assert failed_job.attempt_count == 2
        assert failed_document is not None and failed_document.status == "failed"
    finally:
        settings.upload_directory = previous_directory
        settings.embedding_provider = previous_provider
        settings.job_max_attempts = previous_attempts
        settings.job_retry_backoff_seconds = previous_backoff

import os
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db.models import USER_ROLE, DocumentJob, KnowledgeBase, User
from app.db.session import session_factory
from app.main import create_app

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
    token = create_access_token(
        user_id=user.id,
        role=user.role,
        settings=get_settings(),
    )
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


@pytest.mark.asyncio
async def test_upload_runs_fake_embedding_ingestion_to_ready(
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
                json={"name": f"1C API 测试 {uuid4()}"},
            )
        ).json()
        response = await authenticated_client.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            files={"file": ("制度.txt", "第一条制度。第二条制度。", "text/plain")},
        )
        assert response.status_code == 202
        document_id = response.json()["document_id"]
        state = await authenticated_client.get(f"/api/v1/documents/{document_id}")
        async with session_factory() as session:
            persisted_job = await session.scalar(
                select(DocumentJob).where(
                    DocumentJob.job_type == "ingest_document",
                    DocumentJob.resource_type == "document",
                    DocumentJob.resource_id == document_id,
                )
            )

    finally:
        settings.upload_directory = previous_upload_directory
        settings.embedding_provider = previous_embedding_provider

    assert state.status_code == 200
    assert state.json()["status"] == "ready"
    assert persisted_job is not None
    assert str(persisted_job.knowledge_base_id) == knowledge_base["id"]
    assert persisted_job.owner_user_id is not None
    assert persisted_job.status == "succeeded"


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
        state = await authenticated_client.get(
            f"/api/v1/documents/{response.json()['document_id']}"
        )
    finally:
        settings.upload_directory = previous_upload_directory
        settings.embedding_provider = previous_embedding_provider

    assert response.status_code == 202
    assert state.json()["status"] == "failed"
    assert state.json()["error_code"] == "DOCUMENT_CONTENT_EMPTY"
    assert state.json()["error_message"] == "文档内容为空。"


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

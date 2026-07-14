import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db.models import USER_ROLE, RefreshSession, User
from app.db.models.document import Document
from app.db.models.ingestion_job import IngestionJob
from app.db.models.knowledge_base import KnowledgeBase
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
class ReprocessContext:
    user: User
    client: httpx.AsyncClient


@pytest.fixture
async def reprocess_context() -> AsyncIterator[ReprocessContext]:
    user = User(
        id=uuid4(),
        username=f"reprocess_{uuid4().hex}",
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
            yield ReprocessContext(user, client)
        finally:
            async with session_factory.begin() as session:
                await session.execute(
                    delete(KnowledgeBase).where(KnowledgeBase.owner_id == user.id)
                )
                await session.execute(
                    delete(RefreshSession).where(RefreshSession.user_id == user.id)
                )
                await session.execute(delete(User).where(User.id == user.id))


async def _create_document(tmp_path, owner_id, *, job_status: str) -> tuple[Document, IngestionJob]:
    stored_file_name = f"{uuid4()}.txt"
    (tmp_path / stored_file_name).write_text("员工入职满一年享受五天年假。", encoding="utf-8")
    async with session_factory() as session:
        knowledge_base = KnowledgeBase(name=f"重处理测试-{uuid4()}", owner_id=owner_id)
        session.add(knowledge_base)
        await session.flush()
        document = Document(
            knowledge_base_id=knowledge_base.id,
            original_file_name="员工手册.txt",
            stored_file_name=stored_file_name,
            content_type="text/plain",
            file_extension=".txt",
            file_size=42,
            file_hash=uuid4().hex * 2,
            status="ready",
            error_code="OLD_ERROR",
            error_message="旧错误",
        )
        session.add(document)
        await session.flush()
        job = IngestionJob(
            document_id=document.id,
            status=job_status,
            stage="store" if job_status == "succeeded" else "embed",
        )
        session.add(job)
        await session.commit()
        return document, job


@pytest.mark.asyncio
async def test_reprocess_creates_new_job_and_reuses_ingestion_pipeline(
    tmp_path, reprocess_context: ReprocessContext
) -> None:
    document, old_job = await _create_document(
        tmp_path, reprocess_context.user.id, job_status="succeeded"
    )
    settings = get_settings()
    previous_directory = settings.upload_directory
    previous_provider = settings.embedding_provider
    settings.upload_directory = tmp_path
    settings.embedding_provider = "fake"
    try:
        response = await reprocess_context.client.post(f"/api/v1/documents/{document.id}/reprocess")
    finally:
        settings.upload_directory = previous_directory
        settings.embedding_provider = previous_provider

    assert response.status_code == 202
    assert response.json()["job_id"] != str(old_job.id)
    async with session_factory() as session:
        refreshed = await session.get(Document, document.id)
        jobs = (
            await session.scalars(
                select(IngestionJob).where(IngestionJob.document_id == document.id)
            )
        ).all()
    assert refreshed is not None
    assert refreshed.status == "ready"
    assert refreshed.error_code is None
    assert len(jobs) == 2
    assert any(job.status == "succeeded" and job.id != old_job.id for job in jobs)


@pytest.mark.asyncio
async def test_reprocess_rejects_document_with_active_job(
    tmp_path, reprocess_context: ReprocessContext
) -> None:
    document, _ = await _create_document(tmp_path, reprocess_context.user.id, job_status="running")
    response = await reprocess_context.client.post(f"/api/v1/documents/{document.id}/reprocess")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DOCUMENT_PROCESSING"

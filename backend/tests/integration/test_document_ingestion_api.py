import os
from uuid import uuid4

import httpx
import pytest

from app.core.config import get_settings
from app.main import create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@pytest.mark.asyncio
async def test_upload_runs_fake_embedding_ingestion_to_ready(tmp_path) -> None:
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    previous_embedding_provider = settings.embedding_provider
    settings.upload_directory = tmp_path
    settings.embedding_provider = "fake"
    try:
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            knowledge_base = (
                await client.post(
                    "/api/v1/knowledge-bases",
                    json={"name": f"1C API 测试 {uuid4()}"},
                )
            ).json()
            response = await client.post(
                f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
                files={"file": ("制度.txt", "第一条制度。第二条制度。", "text/plain")},
            )
            assert response.status_code == 202
            document_id = response.json()["document_id"]
            state = await client.get(f"/api/v1/documents/{document_id}")

    finally:
        settings.upload_directory = previous_upload_directory
        settings.embedding_provider = previous_embedding_provider

    assert state.status_code == 200
    assert state.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_whitespace_document_finishes_failed_with_safe_error(tmp_path) -> None:
    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    previous_embedding_provider = settings.embedding_provider
    settings.upload_directory = tmp_path
    settings.embedding_provider = "fake"
    try:
        transport = httpx.ASGITransport(app=create_app())
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            knowledge_base = (
                await client.post(
                    "/api/v1/knowledge-bases",
                    json={"name": f"1C 空文档测试 {uuid4()}"},
                )
            ).json()
            response = await client.post(
                f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
                files={"file": ("空白.txt", "   \n  ", "text/plain")},
            )
            state = await client.get(f"/api/v1/documents/{response.json()['document_id']}")
    finally:
        settings.upload_directory = previous_upload_directory
        settings.embedding_provider = previous_embedding_provider

    assert response.status_code == 202
    assert state.json()["status"] == "failed"
    assert state.json()["error_code"] == "DOCUMENT_CONTENT_EMPTY"
    assert state.json()["error_message"] == "文档内容为空。"

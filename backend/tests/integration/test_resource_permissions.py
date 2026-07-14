import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, update

from app.api.v1.questions import get_rag_service
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db.models import ADMIN_ROLE, USER_ROLE, KnowledgeBase, RefreshSession, User
from app.db.session import session_factory
from app.main import create_app
from app.rag.schemas import QuestionAnswer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


class StubRagService:
    def __init__(self) -> None:
        self.calls: list[UUID] = []

    async def answer(self, knowledge_base_id: UUID, question: str, top_k: int) -> QuestionAnswer:
        self.calls.append(knowledge_base_id)
        return QuestionAnswer(answer="无结果", citations=[], retrieved_chunk_count=0)


@dataclass
class ResourcePermissionContext:
    admin_client: httpx.AsyncClient
    alice_client: httpx.AsyncClient
    bob_client: httpx.AsyncClient
    bob_id: UUID
    rag_service: StubRagService


def _access_token(user: User) -> str:
    return create_access_token(user_id=user.id, role=user.role, settings=get_settings())


@pytest.fixture
async def permission_context(tmp_path: Path) -> AsyncIterator[ResourcePermissionContext]:
    unique = uuid4().hex
    users = [
        User(
            id=uuid4(),
            username=f"resource_admin_{unique}",
            password_hash=hash_password("correct horse battery"),
            role=ADMIN_ROLE,
            is_active=True,
        ),
        User(
            id=uuid4(),
            username=f"resource_alice_{unique}",
            password_hash=hash_password("correct horse battery"),
            role=USER_ROLE,
            is_active=True,
        ),
        User(
            id=uuid4(),
            username=f"resource_bob_{unique}",
            password_hash=hash_password("correct horse battery"),
            role=USER_ROLE,
            is_active=True,
        ),
    ]
    async with session_factory.begin() as session:
        session.add_all(users)

    settings = get_settings()
    previous_upload_directory = settings.upload_directory
    previous_embedding_provider = settings.embedding_provider
    settings.upload_directory = tmp_path
    settings.embedding_provider = "fake"
    rag_service = StubRagService()
    app = create_app()
    app.dependency_overrides[get_rag_service] = lambda: rag_service
    transport = httpx.ASGITransport(app=app)
    clients = [
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {_access_token(user)}"},
        )
        for user in users
    ]
    try:
        yield ResourcePermissionContext(
            clients[0], clients[1], clients[2], users[2].id, rag_service
        )
    finally:
        for client in clients:
            await client.aclose()
        settings.upload_directory = previous_upload_directory
        settings.embedding_provider = previous_embedding_provider
        user_ids = [user.id for user in users]
        async with session_factory.begin() as session:
            await session.execute(delete(KnowledgeBase).where(KnowledgeBase.owner_id.in_(user_ids)))
            await session.execute(
                delete(RefreshSession).where(RefreshSession.user_id.in_(user_ids))
            )
            await session.execute(delete(User).where(User.id.in_(user_ids)))


async def _create_alice_document(
    context: ResourcePermissionContext,
) -> tuple[str, str]:
    knowledge_base_response = await context.alice_client.post(
        "/api/v1/knowledge-bases", json={"name": f"Alice 私有知识库 {uuid4()}"}
    )
    knowledge_base_id = knowledge_base_response.json()["id"]
    upload_response = await context.alice_client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("年假制度.txt", "员工每年有五天年假。", "text/plain")},
    )
    assert upload_response.status_code == 202
    return knowledge_base_id, upload_response.json()["document_id"]


@pytest.mark.asyncio
async def test_documents_and_questions_are_isolated_by_owner(
    permission_context: ResourcePermissionContext,
) -> None:
    knowledge_base_id, document_id = await _create_alice_document(permission_context)

    document_response = await permission_context.bob_client.get(f"/api/v1/documents/{document_id}")
    reprocess_response = await permission_context.bob_client.post(
        f"/api/v1/documents/{document_id}/reprocess"
    )
    list_response = await permission_context.bob_client.get(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents"
    )
    delete_response = await permission_context.bob_client.delete(
        f"/api/v1/documents/{document_id}"
    )
    question_response = await permission_context.bob_client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/questions",
        json={"question": "年假有几天？"},
    )
    admin_response = await permission_context.admin_client.get(f"/api/v1/documents/{document_id}")

    assert document_response.status_code == 404
    assert document_response.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
    assert reprocess_response.status_code == 404
    assert reprocess_response.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
    assert list_response.status_code == 404
    assert list_response.json()["error"]["code"] == "KNOWLEDGE_BASE_NOT_FOUND"
    assert delete_response.status_code == 404
    assert delete_response.json()["error"]["code"] == "DOCUMENT_NOT_FOUND"
    assert question_response.status_code == 404
    assert question_response.json()["error"]["code"] == "KNOWLEDGE_BASE_NOT_FOUND"
    assert permission_context.rag_service.calls == []
    assert admin_response.status_code == 200


@pytest.mark.asyncio
async def test_missing_and_other_users_resources_have_the_same_errors(
    permission_context: ResourcePermissionContext,
) -> None:
    knowledge_base_id, document_id = await _create_alice_document(permission_context)

    other_document = await permission_context.bob_client.get(f"/api/v1/documents/{document_id}")
    missing_document = await permission_context.bob_client.get(f"/api/v1/documents/{uuid4()}")
    other_knowledge_base = await permission_context.bob_client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/questions",
        json={"question": "年假有几天？"},
    )
    missing_knowledge_base = await permission_context.bob_client.post(
        f"/api/v1/knowledge-bases/{uuid4()}/questions",
        json={"question": "年假有几天？"},
    )

    assert other_document.status_code == missing_document.status_code == 404
    assert other_document.json()["error"]["code"] == missing_document.json()["error"]["code"]
    assert other_knowledge_base.status_code == missing_knowledge_base.status_code == 404
    assert (
        other_knowledge_base.json()["error"]["code"]
        == missing_knowledge_base.json()["error"]["code"]
    )


@pytest.mark.asyncio
async def test_upload_authorizes_before_file_validation_or_disk_write(
    permission_context: ResourcePermissionContext, tmp_path: Path
) -> None:
    knowledge_base_response = await permission_context.alice_client.post(
        "/api/v1/knowledge-bases", json={"name": f"Alice 上传隔离 {uuid4()}"}
    )
    knowledge_base_id = knowledge_base_response.json()["id"]

    response = await permission_context.bob_client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("不支持.csv", "敏感内容", "text/csv")},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "KNOWLEDGE_BASE_NOT_FOUND"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_upload_guard_leaves_disabled_user_check_to_route_dependency(
    permission_context: ResourcePermissionContext,
) -> None:
    knowledge_base_response = await permission_context.alice_client.post(
        "/api/v1/knowledge-bases", json={"name": f"停用账号上传 {uuid4()}"}
    )
    knowledge_base_id = knowledge_base_response.json()["id"]
    async with session_factory.begin() as session:
        await session.execute(
            update(User).where(User.id == permission_context.bob_id).values(is_active=False)
        )

    response = await permission_context.bob_client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        files={"file": ("制度.txt", "内容", "text/plain")},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

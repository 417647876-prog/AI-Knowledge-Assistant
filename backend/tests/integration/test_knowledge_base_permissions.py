import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from importlib import import_module
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete

from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import create_access_token, hash_password
from app.db.models import ADMIN_ROLE, USER_ROLE, KnowledgeBase, RefreshSession, User
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
class KnowledgeBasePermissionContext:
    admin: User
    alice: User
    bob: User
    admin_client: httpx.AsyncClient
    alice_client: httpx.AsyncClient
    bob_client: httpx.AsyncClient
    anonymous_client: httpx.AsyncClient


def _access_token(user: User) -> str:
    return create_access_token(
        user_id=user.id,
        role=user.role,
        settings=get_settings(),
    )


@pytest.fixture
async def permission_context() -> AsyncIterator[KnowledgeBasePermissionContext]:
    unique = uuid4().hex
    admin = User(
        id=uuid4(),
        username=f"kb_admin_{unique}",
        password_hash=hash_password("correct horse battery"),
        role=ADMIN_ROLE,
        is_active=True,
    )
    alice = User(
        id=uuid4(),
        username=f"kb_alice_{unique}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    bob = User(
        id=uuid4(),
        username=f"kb_bob_{unique}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    users = [admin, alice, bob]
    async with session_factory.begin() as session:
        session.add_all(users)

    transport = httpx.ASGITransport(app=create_app())
    clients = [
        httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Authorization": f"Bearer {_access_token(user)}"},
        )
        for user in users
    ]
    anonymous_client = httpx.AsyncClient(transport=transport, base_url="http://test")
    context = KnowledgeBasePermissionContext(
        admin,
        alice,
        bob,
        clients[0],
        clients[1],
        clients[2],
        anonymous_client,
    )
    try:
        yield context
    finally:
        for client in [*clients, anonymous_client]:
            await client.aclose()
        user_ids = [user.id for user in users]
        async with session_factory.begin() as session:
            await session.execute(delete(KnowledgeBase).where(KnowledgeBase.owner_id.in_(user_ids)))
            await session.execute(
                delete(RefreshSession).where(RefreshSession.user_id.in_(user_ids))
            )
            await session.execute(delete(User).where(User.id.in_(user_ids)))


@pytest.mark.asyncio
async def test_create_and_list_knowledge_bases_are_isolated_by_owner(
    permission_context: KnowledgeBasePermissionContext,
) -> None:
    alice_create = await permission_context.alice_client.post(
        "/api/v1/knowledge-bases",
        json={
            "name": "Alice KB",
            "description": "private",
            "owner_id": str(permission_context.bob.id),
        },
    )

    assert alice_create.status_code == 201
    assert alice_create.json()["owner_id"] == str(permission_context.alice.id)
    assert alice_create.json()["owner_username"] == permission_context.alice.username

    admin_create = await permission_context.admin_client.post(
        "/api/v1/knowledge-bases",
        json={"name": "Admin KB", "owner_id": str(permission_context.bob.id)},
    )
    assert admin_create.status_code == 201
    assert admin_create.json()["owner_id"] == str(permission_context.admin.id)
    assert admin_create.json()["owner_username"] == permission_context.admin.username

    alice_items = (await permission_context.alice_client.get("/api/v1/knowledge-bases")).json()
    bob_items = (await permission_context.bob_client.get("/api/v1/knowledge-bases")).json()
    admin_items = (await permission_context.admin_client.get("/api/v1/knowledge-bases")).json()

    assert [item["name"] for item in alice_items] == ["Alice KB"]
    assert bob_items == []
    assert [item["name"] for item in admin_items] == ["Alice KB", "Admin KB"]
    assert admin_items[0]["owner_username"] == permission_context.alice.username
    assert admin_items[1]["owner_username"] == permission_context.admin.username


@pytest.mark.asyncio
async def test_anonymous_cannot_create_or_list_knowledge_bases(
    permission_context: KnowledgeBasePermissionContext,
) -> None:
    listed = await permission_context.anonymous_client.get("/api/v1/knowledge-bases")
    created = await permission_context.anonymous_client.post(
        "/api/v1/knowledge-bases", json={"name": "Anonymous KB"}
    )

    assert listed.status_code == 401
    assert listed.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert created.status_code == 401
    assert created.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.asyncio
async def test_accessible_knowledge_base_hides_other_users_resources(
    permission_context: KnowledgeBasePermissionContext,
) -> None:
    service = import_module("app.authorization.service")
    async with session_factory.begin() as session:
        knowledge_base = KnowledgeBase(
            name="Alice private KB", owner_id=permission_context.alice.id
        )
        session.add(knowledge_base)
        await session.flush()
        knowledge_base_id = knowledge_base.id

    async with session_factory() as session:
        with pytest.raises(AppError) as forbidden:
            await service.get_accessible_knowledge_base(
                session, permission_context.bob, knowledge_base_id
            )
        with pytest.raises(AppError) as missing:
            await service.get_accessible_knowledge_base(session, permission_context.bob, uuid4())
        admin_result = await service.get_accessible_knowledge_base(
            session,
            permission_context.admin,
            knowledge_base_id,
            for_update=True,
        )

    assert forbidden.value.status_code == 404
    assert forbidden.value.code == "KNOWLEDGE_BASE_NOT_FOUND"
    assert missing.value.status_code == 404
    assert missing.value.code == forbidden.value.code
    assert admin_result.id == knowledge_base_id

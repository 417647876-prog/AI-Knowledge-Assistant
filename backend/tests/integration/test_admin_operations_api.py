import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db.models import ADMIN_ROLE, USER_ROLE, DocumentJob, KnowledgeBase, User
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

FORBIDDEN_KEYS = {
    "name",
    "file_name",
    "original_file_name",
    "content",
    "question",
    "answer",
    "prompt",
    "download_url",
    "citation_content",
}


@dataclass
class OperationsApiContext:
    admin: User
    user: User
    admin_client: httpx.AsyncClient
    user_client: httpx.AsyncClient


def _access_token(user: User) -> str:
    return create_access_token(user_id=user.id, role=user.role, settings=get_settings())


@pytest.fixture
async def operations_api() -> AsyncIterator[OperationsApiContext]:
    suffix = uuid4().hex
    admin = User(
        id=uuid4(),
        username=f"operations_admin_{suffix}",
        password_hash=hash_password("correct horse battery"),
        role=ADMIN_ROLE,
        is_active=True,
    )
    user = User(
        id=uuid4(),
        username=f"operations_user_{suffix}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    async with session_factory.begin() as session:
        session.add_all([admin, user])

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    admin_client = httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {_access_token(admin)}"},
    )
    user_client = httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {_access_token(user)}"},
    )
    try:
        yield OperationsApiContext(admin, user, admin_client, user_client)
    finally:
        await admin_client.aclose()
        await user_client.aclose()
        async with session_factory.begin() as session:
            await delete_owned_knowledge_bases(session, [admin.id, user.id])
            await session.execute(delete(User).where(User.id.in_([admin.id, user.id])))


def _assert_no_sensitive_keys(value: object) -> None:
    if isinstance(value, dict):
        assert not (FORBIDDEN_KEYS & value.keys())
        for child in value.values():
            _assert_no_sensitive_keys(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_sensitive_keys(child)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["overview", "users", "jobs", "quality"])
async def test_operations_endpoints_require_admin_and_never_return_content_keys(
    operations_api: OperationsApiContext, path: str
) -> None:
    forbidden = await operations_api.user_client.get(f"/api/v1/admin/operations/{path}")
    allowed = await operations_api.admin_client.get(f"/api/v1/admin/operations/{path}")

    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "PERMISSION_DENIED"
    assert allowed.status_code == 200
    _assert_no_sensitive_keys(allowed.json())


@pytest.mark.asyncio
async def test_operations_empty_scope_returns_zero_aggregates_and_rejects_invalid_time_range(
    operations_api: OperationsApiContext,
) -> None:
    response = await operations_api.admin_client.get(
        "/api/v1/admin/operations/overview",
        params={
            "start_at": "2026-07-01T00:00:00+00:00",
            "end_at": "2026-07-01T00:01:00+00:00",
        },
    )
    assert response.status_code == 200
    assert response.json()["account_total"] == 0
    assert response.json()["document_total"] == 0
    assert response.json()["effective_document_bytes"] == 0

    naive = await operations_api.admin_client.get(
        "/api/v1/admin/operations/overview", params={"start_at": "2026-07-01T00:00:00"}
    )
    reversed_range = await operations_api.admin_client.get(
        "/api/v1/admin/operations/overview",
        params={
            "start_at": "2026-07-02T00:00:00+00:00",
            "end_at": "2026-07-01T00:00:00+00:00",
        },
    )
    assert naive.status_code == 422
    assert reversed_range.status_code == 422


@pytest.mark.asyncio
async def test_jobs_are_page_bounded_and_use_created_at_plus_uuid_cursor(
    operations_api: OperationsApiContext,
) -> None:
    created_at = datetime(2026, 7, 3, tzinfo=UTC)
    async with session_factory.begin() as session:
        knowledge_base = KnowledgeBase(
            id=uuid4(), name="不得出现在运营接口", owner_id=operations_api.user.id
        )
        session.add(knowledge_base)
        await session.flush()
        jobs = [
            DocumentJob(
                id=uuid4(),
                job_type="ingest_document",
                resource_type="document",
                resource_id=uuid4(),
                owner_user_id=operations_api.user.id,
                knowledge_base_id=knowledge_base.id,
                status="succeeded",
                run_after=created_at,
                attempt_count=1,
                max_attempts=3,
                stage="completed",
                created_at=created_at,
            )
            for _ in range(2)
        ]
        session.add_all(jobs)

    first = await operations_api.admin_client.get(
        "/api/v1/admin/operations/jobs",
        params={
            "limit": 1,
            "start_at": "2026-07-03T00:00:00+00:00",
            "end_at": "2026-07-04T00:00:00+00:00",
        },
    )
    assert first.status_code == 200
    assert len(first.json()["items"]) == 1
    assert first.json()["next_cursor"] is not None

    cursor = first.json()["next_cursor"]
    second = await operations_api.admin_client.get(
        "/api/v1/admin/operations/jobs",
        params={
            "limit": 1,
            "start_at": "2026-07-03T00:00:00+00:00",
            "end_at": "2026-07-04T00:00:00+00:00",
            "cursor_created_at": cursor["created_at"],
            "cursor_id": cursor["id"],
        },
    )
    assert second.status_code == 200
    assert {UUID(first.json()["items"][0]["id"]), UUID(second.json()["items"][0]["id"])} == {
        job.id for job in jobs
    }

    too_large = await operations_api.admin_client.get(
        "/api/v1/admin/operations/jobs", params={"limit": 101}
    )
    assert too_large.status_code == 422

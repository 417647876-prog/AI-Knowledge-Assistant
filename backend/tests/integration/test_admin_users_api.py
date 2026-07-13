import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete, select, update

from app.api.v1.admin_users import AdminUserResponse, AdminUserUpdate, update_user
from app.auth.service import AuthService
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.core.security import (
    create_access_token,
    hash_password,
    verify_password,
)
from app.db.models import ADMIN_ROLE, USER_ROLE, RefreshSession, User
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
class AdminApiContext:
    admin: User
    user: User
    admin_client: httpx.AsyncClient
    user_client: httpx.AsyncClient
    created_user_ids: list[UUID] = field(default_factory=list)
    created_usernames: list[str] = field(default_factory=list)


def _access_token(user: User) -> str:
    return create_access_token(
        user_id=user.id,
        role=user.role,
        settings=get_settings(),
    )


@pytest.fixture
async def admin_api() -> AsyncIterator[AdminApiContext]:
    unique = uuid4().hex
    admin = User(
        id=uuid4(),
        username=f"admin_{unique}",
        password_hash=hash_password("correct horse battery"),
        role=ADMIN_ROLE,
        is_active=True,
    )
    user = User(
        id=uuid4(),
        username=f"user_{unique}",
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
    context = AdminApiContext(admin, user, admin_client, user_client)
    try:
        yield context
    finally:
        await admin_client.aclose()
        await user_client.aclose()
        async with session_factory() as session:
            fallback_ids = list(
                await session.scalars(
                    select(User.id).where(
                        User.username.in_(context.created_usernames)
                    )
                )
            )
        user_ids = [admin.id, user.id, *context.created_user_ids, *fallback_ids]
        async with session_factory.begin() as session:
            await session.execute(
                delete(RefreshSession).where(RefreshSession.user_id.in_(user_ids))
            )
            await session.execute(delete(User).where(User.id.in_(user_ids)))


@pytest.mark.asyncio
async def test_admin_can_list_and_create_users_but_regular_user_cannot(
    admin_api: AdminApiContext,
) -> None:
    listed = await admin_api.admin_client.get("/api/v1/admin/users")
    forbidden = await admin_api.user_client.get("/api/v1/admin/users")

    assert listed.status_code == 200
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "PERMISSION_DENIED"
    listed_admin = next(
        item for item in listed.json() if item["id"] == str(admin_api.admin.id)
    )
    assert listed_admin["created_at"]
    assert listed_admin["updated_at"]

    username = f" Alice_{uuid4().hex} "
    admin_api.created_usernames.append(username.strip().lower())
    created = await admin_api.admin_client.post(
        "/api/v1/admin/users",
        json={
            "username": username,
            "password": "temporary pass 123",
            "role": "user",
        },
    )

    assert created.status_code == 201
    admin_api.created_user_ids.append(UUID(created.json()["id"]))
    assert created.json()["username"] == username.strip().lower()

    duplicate = await admin_api.admin_client.post(
        "/api/v1/admin/users",
        json={
            "username": username.strip().upper(),
            "password": "temporary pass 456",
            "role": "user",
        },
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "USERNAME_ALREADY_EXISTS"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "field"),
    [
        ({"username": "ab", "password": "temporary pass 123", "role": "user"}, "username"),
        (
            {"username": "invalid name", "password": "temporary pass 123", "role": "user"},
            "username",
        ),
        ({"username": "valid-name", "password": "too-short", "role": "user"}, "password"),
        (
            {"username": "valid-name", "password": "temporary pass 123", "role": "owner"},
            "role",
        ),
    ],
)
async def test_create_user_rejects_input_outside_fixed_boundaries(
    admin_api: AdminApiContext,
    payload: dict[str, str],
    field: str,
) -> None:
    response = await admin_api.admin_client.post("/api/v1/admin/users", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][-1] == field


@pytest.mark.asyncio
async def test_admin_cannot_deactivate_self(admin_api: AdminApiContext) -> None:
    response = await admin_api.admin_client.patch(
        f"/api/v1/admin/users/{admin_api.admin.id}",
        json={"is_active": False},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CANNOT_DEACTIVATE_SELF"


@pytest.mark.asyncio
async def test_last_active_admin_cannot_be_demoted(admin_api: AdminApiContext) -> None:
    response = await admin_api.admin_client.patch(
        f"/api/v1/admin/users/{admin_api.admin.id}",
        json={"role": "user"},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "LAST_ADMIN_REQUIRED"


@pytest.mark.asyncio
async def test_concurrent_admin_removal_keeps_exactly_one_active_admin(
    admin_api: AdminApiContext,
) -> None:
    async with session_factory.begin() as session:
        target = await session.get(User, admin_api.user.id)
        assert target is not None
        target.role = ADMIN_ROLE
    admin_api.user.role = ADMIN_ROLE

    async with (
        session_factory() as first_session,
        session_factory() as second_session,
    ):
        results = await asyncio.wait_for(
            asyncio.gather(
                update_user(
                    admin_api.admin.id,
                    AdminUserUpdate(role="user"),
                    admin_api.user,
                    first_session,
                    get_settings(),
                ),
                update_user(
                    admin_api.user.id,
                    AdminUserUpdate(is_active=False),
                    admin_api.admin,
                    second_session,
                    get_settings(),
                ),
                return_exceptions=True,
            ),
            timeout=10,
        )

    successes = [result for result in results if isinstance(result, AdminUserResponse)]
    errors = [result for result in results if isinstance(result, AppError)]
    assert len(successes) == 1
    assert len(errors) == 1
    assert errors[0].code == "LAST_ADMIN_REQUIRED"

    async with session_factory() as session:
        admins = list(
            await session.scalars(
                select(User).where(User.id.in_([admin_api.admin.id, admin_api.user.id]))
            )
        )
    assert sum(user.role == ADMIN_ROLE and user.is_active for user in admins) == 1


async def _add_refresh_sessions(user_id: UUID, count: int = 2) -> None:
    now = datetime.now(UTC)
    async with session_factory.begin() as session:
        session.add_all(
            [
                RefreshSession(
                    id=uuid4(),
                    user_id=user_id,
                    token_hash=uuid4().hex + uuid4().hex,
                    expires_at=now + timedelta(days=1),
                )
                for _ in range(count)
            ]
        )


async def _assert_all_sessions_revoked(user_id: UUID) -> None:
    async with session_factory() as session:
        revoked_values = list(
            await session.scalars(
                select(RefreshSession.revoked_at).where(
                    RefreshSession.user_id == user_id
                )
            )
        )
    assert len(revoked_values) == 2
    assert all(value is not None for value in revoked_values)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["deactivate", "demote", "reset-password"])
async def test_security_sensitive_changes_revoke_all_target_refresh_sessions(
    admin_api: AdminApiContext,
    operation: str,
) -> None:
    target = admin_api.user
    if operation == "demote":
        async with session_factory.begin() as session:
            stored = await session.get(User, target.id)
            assert stored is not None
            stored.role = ADMIN_ROLE
        target.role = ADMIN_ROLE
    await _add_refresh_sessions(target.id)

    if operation == "reset-password":
        response = await admin_api.admin_client.post(
            f"/api/v1/admin/users/{target.id}/reset-password",
            json={"password": "replacement pass 123"},
        )
    else:
        payload = {"is_active": False} if operation == "deactivate" else {"role": "user"}
        response = await admin_api.admin_client.patch(
            f"/api/v1/admin/users/{target.id}",
            json=payload,
        )

    assert response.status_code == 200
    await _assert_all_sessions_revoked(target.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("operation", "failure_kind"),
    [
        ("deactivate", "runtime"),
        ("demote", "runtime"),
        ("reset-password", "runtime"),
        ("deactivate", "cancel"),
    ],
)
async def test_revoke_failure_rolls_back_user_and_all_refresh_sessions(
    admin_api: AdminApiContext,
    operation: str,
    failure_kind: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = admin_api.user
    if operation == "demote":
        async with session_factory.begin() as session:
            stored = await session.get(User, target.id)
            assert stored is not None
            stored.role = ADMIN_ROLE
        target.role = ADMIN_ROLE
    await _add_refresh_sessions(target.id)

    async def partially_revoke_then_fail(
        service: AuthService,
        user_id: UUID,
    ) -> None:
        await service._session.execute(  # noqa: SLF001 - 故意注入事务中途失败
            update(RefreshSession)
            .where(RefreshSession.user_id == user_id)
            .values(revoked_at=datetime.now(UTC))
        )
        if failure_kind == "cancel":
            raise asyncio.CancelledError
        raise RuntimeError("injected revoke failure")

    monkeypatch.setattr(
        AuthService,
        "revoke_all_for_user_in_transaction",
        partially_revoke_then_fail,
        raising=False,
    )

    expected_message = (
        "No response returned" if failure_kind == "cancel" else "injected revoke failure"
    )
    with pytest.raises(RuntimeError, match=expected_message):
        if operation == "reset-password":
            await admin_api.admin_client.post(
                f"/api/v1/admin/users/{target.id}/reset-password",
                json={"password": "replacement pass 123"},
            )
        else:
            payload = (
                {"is_active": False}
                if operation == "deactivate"
                else {"role": "user"}
            )
            await admin_api.admin_client.patch(
                f"/api/v1/admin/users/{target.id}",
                json=payload,
            )

    async with session_factory() as session:
        stored = await session.get(User, target.id)
        assert stored is not None
        revoked_values = list(
            await session.scalars(
                select(RefreshSession.revoked_at).where(
                    RefreshSession.user_id == target.id
                )
            )
        )
    assert stored.role == target.role
    assert stored.is_active is True
    assert verify_password("correct horse battery", stored.password_hash)
    assert revoked_values == [None, None]

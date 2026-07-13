import os
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, update

from app.core.config import Settings, get_settings
from app.core.security import create_access_token, hash_password
from app.db.models import ADMIN_ROLE, RefreshSession, User
from app.db.session import session_factory
from app.main import create_app

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


def assert_session_cookie(cookie: str, *, secure: bool) -> None:
    assert cookie.startswith("refresh_token=")
    assert "HttpOnly" in cookie
    assert "Max-Age=604800" in cookie
    assert "Path=/api/v1/auth" in cookie
    assert "samesite=lax" in cookie.casefold()
    assert ("Secure" in cookie) is secure


@pytest.fixture
async def auth_user() -> AsyncIterator[User]:
    user = User(
        id=uuid4(),
        username=f"auth_admin_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=ADMIN_ROLE,
        is_active=True,
    )
    async with session_factory() as session:
        session.add(user)
        await session.commit()
    try:
        yield user
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(RefreshSession).where(RefreshSession.user_id == user.id))
            await session.execute(delete(User).where(User.id == user.id))


@pytest.mark.asyncio
async def test_login_refresh_logout_me_and_inactive_user(auth_user: User) -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "username": auth_user.username,
                "password": "correct horse battery",
            },
            headers={"X-Request-ID": "auth-login-001"},
        )

        assert login.status_code == 200
        assert login.json()["token_type"] == "bearer"
        assert login.json()["user"]["role"] == "admin"
        assert login.headers["X-Request-ID"] == "auth-login-001"
        login_cookie = login.headers["set-cookie"]
        assert_session_cookie(login_cookie, secure=False)
        first_refresh = client.cookies.get("refresh_token")
        assert first_refresh

        me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert me.status_code == 200
        assert me.json()["username"] == auth_user.username

        invalid_origin = await client.post(
            "/api/v1/auth/refresh",
            headers={"X-Request-ID": "auth-origin-001"},
        )
        assert invalid_origin.status_code == 403
        assert invalid_origin.json() == {
            "error": {
                "code": "INVALID_ORIGIN",
                "message": "请求来源不受信任。",
                "request_id": "auth-origin-001",
            }
        }

        refreshed = await client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": "http://localhost:5173"},
        )
        assert refreshed.status_code == 200
        second_refresh = client.cookies.get("refresh_token")
        assert second_refresh and second_refresh != first_refresh
        assert_session_cookie(refreshed.headers["set-cookie"], secure=False)

        client.cookies.set("refresh_token", first_refresh, path="/api/v1/auth")
        replay = await client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": "http://localhost:5173"},
        )
        assert replay.status_code == 401
        assert replay.json()["error"]["code"] == "TOKEN_REVOKED"

        client.cookies.set("refresh_token", second_refresh, path="/api/v1/auth")
        logout = await client.post(
            "/api/v1/auth/logout",
            headers={"Origin": "http://localhost:5173"},
        )
        assert logout.status_code == 204
        cleared_cookie = logout.headers["set-cookie"]
        assert "Path=/api/v1/auth" in cleared_cookie
        assert "Max-Age=0" in cleared_cookie
        assert "HttpOnly" in cleared_cookie
        assert "samesite=lax" in cleared_cookie.casefold()
        assert "Secure" not in cleared_cookie

        async with session_factory.begin() as session:
            await session.execute(
                update(User).where(User.id == auth_user.id).values(is_active=False)
            )
        inactive_me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {login.json()['access_token']}"},
        )
        assert inactive_me.status_code == 401
        assert inactive_me.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

        missing_user_token = create_access_token(
            user_id=uuid4(), role="admin", settings=get_settings()
        )
        missing_user_me = await client.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {missing_user_token}"},
        )
        assert missing_user_me.status_code == 401
        assert missing_user_me.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"

        missing_bearer_me = await client.get("/api/v1/auth/me")
        assert missing_bearer_me.status_code == 401
        assert missing_bearer_me.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.asyncio
async def test_login_and_refresh_cookie_use_secure_setting(auth_user: User) -> None:
    secure_settings = Settings(_env_file=None, refresh_cookie_secure=True)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: secure_settings
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        login = await client.post(
            "/api/v1/auth/login",
            json={
                "username": auth_user.username,
                "password": "correct horse battery",
            },
        )
        assert login.status_code == 200
        assert_session_cookie(login.headers["set-cookie"], secure=True)

        refreshed = await client.post(
            "/api/v1/auth/refresh",
            headers={"Origin": "http://localhost:5173"},
        )
        assert refreshed.status_code == 200
        assert_session_cookie(refreshed.headers["set-cookie"], secure=True)

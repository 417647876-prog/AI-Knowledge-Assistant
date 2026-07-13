import asyncio
import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from app.api.auth_dependencies import require_admin
from app.auth.service import AuthService
from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.security import create_refresh_token, hash_password, hash_refresh_secret
from app.db.models import ADMIN_ROLE, RefreshSession, User


class AsyncTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        return None


def make_session(*scalar_results: object) -> MagicMock:
    session = MagicMock()
    session.begin.return_value = AsyncTransaction()
    session.scalar = AsyncMock(side_effect=scalar_results)
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    return session


def make_service(session: MagicMock, now: datetime) -> AuthService:
    settings = Settings(_env_file=None, jwt_secret_key="x" * 64)
    return AuthService(session=session, settings=settings, now=lambda: now)


@pytest.mark.asyncio
async def test_login_issues_access_and_persisted_refresh_session() -> None:
    now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    user = User(
        id=uuid4(),
        username="admin",
        password_hash=hash_password("correct horse battery"),
        role=ADMIN_ROLE,
        is_active=True,
    )
    session = make_session(user)

    issued = await make_service(session, now).login(" Admin ", "correct horse battery")

    assert issued.user is user
    assert issued.expires_in == 15 * 60
    assert issued.access_token
    persisted = session.add.call_args.args[0]
    refresh_id, refresh_secret = issued.refresh_token.split(".", 1)
    assert str(persisted.id) == refresh_id
    assert persisted.user_id == user.id
    assert persisted.token_hash == hash_refresh_secret(refresh_secret)
    assert persisted.expires_at == now + timedelta(days=7)


@pytest.mark.asyncio
async def test_unknown_username_still_runs_virtual_password_verification() -> None:
    service = make_service(make_session(None), datetime.now(UTC))

    with (
        patch("app.auth.service.verify_password", return_value=False) as verify,
        pytest.raises(AppError) as error,
    ):
        await service.login("missing", "wrong password")

    assert error.value.code == "INVALID_CREDENTIALS"
    verify.assert_called_once()
    assert verify.call_args.args[0] == "wrong password"


@pytest.mark.asyncio
async def test_login_password_verification_does_not_block_event_loop() -> None:
    service = make_service(make_session(None), datetime.now(UTC))
    release = threading.Event()
    released_while_verifying = False

    def slow_verify(_password: str, _password_hash: str) -> bool:
        nonlocal released_while_verifying
        released_while_verifying = release.wait(timeout=0.5)
        return False

    async def heartbeat() -> None:
        await asyncio.sleep(0)
        release.set()

    with (
        patch("app.auth.service.verify_password", side_effect=slow_verify),
        pytest.raises(AppError),
    ):
        login_task = asyncio.create_task(service.login("missing", "wrong password"))
        await heartbeat()
        await login_task

    assert released_while_verifying is True


@pytest.mark.asyncio
@pytest.mark.parametrize("is_active", [True, False])
async def test_wrong_password_and_inactive_user_share_invalid_credentials(
    is_active: bool,
) -> None:
    user = User(
        id=uuid4(),
        username="reader",
        password_hash=hash_password("correct password"),
        role="user",
        is_active=is_active,
    )
    password = "wrong password" if is_active else "correct password"

    with pytest.raises(AppError) as error:
        await make_service(make_session(user), datetime.now(UTC)).login(user.username, password)

    assert error.value.code == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_refresh_rotates_session_and_replay_does_not_issue_token() -> None:
    now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    token = create_refresh_token()
    user = User(
        id=uuid4(),
        username="reader",
        password_hash="unused",
        role="user",
        is_active=True,
    )
    old_session = RefreshSession(
        id=token.session_id,
        user_id=user.id,
        token_hash=hash_refresh_secret(token.secret),
        expires_at=now + timedelta(days=1),
    )
    session = make_session(old_session, user)

    issued = await make_service(session, now).refresh(token.raw)

    lock_query = session.scalar.await_args_list[0].args[0]
    assert "FOR UPDATE" in str(lock_query.compile(dialect=postgresql.dialect()))
    replacement = session.add.call_args.args[0]
    assert old_session.revoked_at == now
    assert old_session.replaced_by_id == replacement.id
    assert issued.refresh_token.startswith(f"{replacement.id}.")

    replay_session = make_session(old_session)
    with (
        patch("app.auth.service.create_access_token") as create_access,
        pytest.raises(AppError) as error,
    ):
        await make_service(replay_session, now + timedelta(seconds=1)).refresh(token.raw)

    assert error.value.code == "TOKEN_REVOKED"
    create_access.assert_not_called()
    replay_session.add.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw_token", "expires_at", "expected_code"),
    [
        ("wrong-secret", timedelta(days=1), "TOKEN_INVALID"),
        (None, timedelta(seconds=-1), "TOKEN_EXPIRED"),
    ],
)
async def test_refresh_rejects_wrong_secret_and_expired_session(
    raw_token: str | None,
    expires_at: timedelta,
    expected_code: str,
) -> None:
    now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    token = create_refresh_token()
    stored = RefreshSession(
        id=token.session_id,
        user_id=uuid4(),
        token_hash=hash_refresh_secret(token.secret),
        expires_at=now + expires_at,
    )
    attempted_token = f"{token.session_id}.{raw_token}" if raw_token is not None else token.raw
    session = make_session(stored)

    with pytest.raises(AppError) as error:
        await make_service(session, now).refresh(attempted_token)

    assert error.value.code == expected_code
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_logout_revokes_matching_session() -> None:
    now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
    token = create_refresh_token()
    stored = RefreshSession(
        id=token.session_id,
        user_id=uuid4(),
        token_hash=hash_refresh_secret(token.secret),
        expires_at=now + timedelta(days=1),
    )

    await make_service(make_session(stored), now).logout(token.raw)

    assert stored.revoked_at == now


@pytest.mark.asyncio
async def test_revoke_all_updates_active_sessions_for_user() -> None:
    user_id = uuid4()
    session = make_session()

    await make_service(session, datetime.now(UTC)).revoke_all_for_user(user_id)

    session.begin.assert_called_once_with()
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_revoke_all_in_transaction_leaves_commit_to_caller() -> None:
    user_id = uuid4()
    session = make_session()

    await make_service(session, datetime.now(UTC)).revoke_all_for_user_in_transaction(user_id)

    session.begin.assert_not_called()
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_require_admin_rejects_regular_user() -> None:
    user = User(
        id=uuid4(),
        username="reader",
        password_hash="unused",
        role="user",
        is_active=True,
    )

    with pytest.raises(AppError) as error:
        await require_admin(user)

    assert error.value.code == "PERMISSION_DENIED"
    assert error.value.status_code == 403

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.exceptions import AppError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    hash_password,
    hash_refresh_secret,
    verify_password,
)
from app.db.models import RefreshSession, User

_DUMMY_PASSWORD_HASH = hash_password("virtual-password-check-only")


@dataclass(frozen=True)
class IssuedSession:
    access_token: str
    refresh_token: str
    expires_in: int
    user: User


class AuthService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        settings: Settings,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._session = session
        self._settings = settings
        self._now = now or (lambda: datetime.now(UTC))

    async def login(self, username: str, password: str) -> IssuedSession:
        normalized_username = username.strip().casefold()
        async with self._session.begin():
            user = await self._session.scalar(
                select(User).where(User.username == normalized_username)
            )
            password_hash = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
            password_matches = verify_password(password, password_hash)
            if user is None or not password_matches or not user.is_active:
                raise _invalid_credentials()
            return self._issue_session(user, self._now())

    async def refresh(self, raw_refresh_token: str) -> IssuedSession:
        session_id, secret = _parse_refresh_token(raw_refresh_token)
        now = self._now()
        async with self._session.begin():
            stored = await self._session.scalar(
                select(RefreshSession).where(RefreshSession.id == session_id).with_for_update()
            )
            if stored is None or not secrets.compare_digest(
                stored.token_hash, hash_refresh_secret(secret)
            ):
                raise _token_error("TOKEN_INVALID", "刷新令牌无效。")
            if stored.revoked_at is not None:
                raise _token_error("TOKEN_REVOKED", "刷新令牌已被撤销。")
            if stored.expires_at <= now:
                raise _token_error("TOKEN_EXPIRED", "刷新令牌已过期。")

            user = await self._session.scalar(select(User).where(User.id == stored.user_id))
            if user is None or not user.is_active:
                raise _token_error("UNAUTHORIZED", "用户未登录或账号已停用。")

            stored.revoked_at = now
            replacement_token = create_refresh_token()
            replacement = self._build_refresh_session(
                user_id=user.id,
                token_id=replacement_token.session_id,
                secret=replacement_token.secret,
                now=now,
            )
            self._session.add(replacement)
            await self._session.flush()
            stored.replaced_by_id = replacement.id
            return self._issued_result(user, replacement_token.raw, now)

    async def logout(self, raw_refresh_token: str | None) -> None:
        if raw_refresh_token is None:
            return
        try:
            session_id, secret = _parse_refresh_token(raw_refresh_token)
        except AppError:
            return

        async with self._session.begin():
            stored = await self._session.scalar(
                select(RefreshSession).where(RefreshSession.id == session_id).with_for_update()
            )
            if (
                stored is not None
                and stored.revoked_at is None
                and secrets.compare_digest(stored.token_hash, hash_refresh_secret(secret))
            ):
                stored.revoked_at = self._now()

    async def revoke_all_for_user(self, user_id: UUID) -> None:
        async with self._session.begin():
            await self.revoke_all_for_user_in_transaction(user_id)

    async def revoke_all_for_user_in_transaction(self, user_id: UUID) -> None:
        await self._session.execute(
            update(RefreshSession)
            .where(
                RefreshSession.user_id == user_id,
                RefreshSession.revoked_at.is_(None),
            )
            .values(revoked_at=self._now())
        )

    def _issue_session(self, user: User, now: datetime) -> IssuedSession:
        refresh_token = create_refresh_token()
        self._session.add(
            self._build_refresh_session(
                user_id=user.id,
                token_id=refresh_token.session_id,
                secret=refresh_token.secret,
                now=now,
            )
        )
        return self._issued_result(user, refresh_token.raw, now)

    def _build_refresh_session(
        self,
        *,
        user_id: UUID,
        token_id: UUID,
        secret: str,
        now: datetime,
    ) -> RefreshSession:
        return RefreshSession(
            id=token_id,
            user_id=user_id,
            token_hash=hash_refresh_secret(secret),
            expires_at=now + timedelta(days=self._settings.refresh_token_expire_days),
        )

    def _issued_result(self, user: User, raw_refresh_token: str, now: datetime) -> IssuedSession:
        return IssuedSession(
            access_token=create_access_token(
                user_id=user.id,
                role=user.role,
                settings=self._settings,
                now=now,
            ),
            refresh_token=raw_refresh_token,
            expires_in=self._settings.access_token_expire_minutes * 60,
            user=user,
        )


def _parse_refresh_token(raw_refresh_token: str) -> tuple[UUID, str]:
    try:
        raw_session_id, secret = raw_refresh_token.split(".", 1)
        session_id = UUID(raw_session_id)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _token_error("TOKEN_INVALID", "刷新令牌无效。") from exc
    if not secret:
        raise _token_error("TOKEN_INVALID", "刷新令牌无效。")
    return session_id, secret


def _invalid_credentials() -> AppError:
    return AppError(
        code="INVALID_CREDENTIALS",
        message="用户名或密码错误。",
        status_code=401,
    )


def _token_error(code: str, message: str) -> AppError:
    return AppError(code=code, message=message, status_code=401)

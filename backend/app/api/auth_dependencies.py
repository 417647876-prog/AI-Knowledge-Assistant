from typing import Annotated

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.security import TokenValidationError, decode_access_token
from app.db.models import ADMIN_ROLE, User
from app.db.session import get_session

bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(bearer_scheme)
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> User:
    if credentials is None or credentials.scheme.casefold() != "bearer":
        raise _unauthorized()
    try:
        claims = decode_access_token(credentials.credentials, settings)
    except TokenValidationError as exc:
        raise AppError(
            code=exc.code,
            message="访问令牌无效或已过期。",
            status_code=401,
        ) from exc

    user = await session.scalar(select(User).where(User.id == claims.user_id))
    if user is None or not user.is_active:
        raise _unauthorized()
    return user


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    if user.role != ADMIN_ROLE:
        raise AppError(
            code="PERMISSION_DENIED", message="需要管理员权限。", status_code=403
        )
    return user


def _unauthorized() -> AppError:
    return AppError(
        code="AUTHENTICATION_REQUIRED",
        message="用户未登录或账号已停用。",
        status_code=401,
    )

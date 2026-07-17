from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.api.auth_dependencies import require_admin
from app.auth.schemas import CurrentUserResponse
from app.auth.service import AuthService
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.core.security import hash_password
from app.db.models import ADMIN_ROLE, User, UserQuota
from app.db.session import get_session

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin-users"])


class AdminUserCreate(BaseModel):
    username: Annotated[
        str,
        StringConstraints(
            min_length=3,
            max_length=50,
            pattern=r"^[A-Za-z0-9._-]+$",
        ),
    ]
    password: Annotated[str, StringConstraints(min_length=6, max_length=128)]
    role: Literal["admin", "user"]

    @field_validator("username", mode="before")
    @classmethod
    def normalize_username(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().lower()
        return value


class AdminUserUpdate(BaseModel):
    role: Literal["admin", "user"] | None = None
    is_active: bool | None = None


class AdminPasswordReset(BaseModel):
    password: Annotated[str, StringConstraints(min_length=6, max_length=128)]


class AdminUserResponse(CurrentUserResponse):
    created_at: datetime
    updated_at: datetime


class AdminQuotaUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    daily_question_limit: int | None = Field(default=None, ge=0)
    daily_upload_limit: int | None = Field(default=None, ge=0)
    storage_bytes_limit: int | None = Field(default=None, ge=0)


class AdminQuotaResponse(BaseModel):
    daily_question_limit: int | None
    daily_upload_limit: int | None
    storage_bytes_limit: int | None


@router.get("", response_model=list[AdminUserResponse])
async def list_users(
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AdminUserResponse]:
    users = await session.scalars(select(User).order_by(User.username, User.id))
    return [AdminUserResponse.model_validate(user) for user in users]


@router.post(
    "",
    response_model=AdminUserResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    payload: AdminUserCreate,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminUserResponse:
    normalized_username = payload.username.strip().lower()
    existing = await session.scalar(select(User.id).where(User.username == normalized_username))
    if existing is not None:
        raise _username_exists()

    password_hash = await run_in_threadpool(hash_password, payload.password)
    user = User(
        username=normalized_username,
        password_hash=password_hash,
        role=payload.role,
        is_active=True,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise _username_exists() from exc
    return AdminUserResponse.model_validate(user)


async def _get_quota_target(session: AsyncSession, user_id: UUID) -> User:
    target = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if target is None:
        raise _user_not_found()
    return target


@router.get("/{user_id}/quota", response_model=AdminQuotaResponse)
async def get_user_quota(
    user_id: UUID,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminQuotaResponse:
    await _get_quota_target(session, user_id)
    quota = await session.get(UserQuota, user_id)
    return AdminQuotaResponse(
        daily_question_limit=quota.daily_question_limit if quota else None,
        daily_upload_limit=quota.daily_upload_limit if quota else None,
        storage_bytes_limit=quota.storage_bytes_limit if quota else None,
    )


@router.put("/{user_id}/quota", response_model=AdminQuotaResponse)
async def update_user_quota(
    user_id: UUID,
    payload: AdminQuotaUpdate,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AdminQuotaResponse:
    await _get_quota_target(session, user_id)
    quota = await session.get(UserQuota, user_id, with_for_update=True)
    if quota is None:
        quota = UserQuota(user_id=user_id)
        session.add(quota)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(quota, field, value)
    await session.commit()
    await session.refresh(quota)
    return AdminQuotaResponse(
        daily_question_limit=quota.daily_question_limit,
        daily_upload_limit=quota.daily_upload_limit,
        storage_bytes_limit=quota.storage_bytes_limit,
    )


@router.patch("/{user_id}", response_model=AdminUserResponse)
async def update_user(
    user_id: UUID,
    payload: AdminUserUpdate,
    admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AdminUserResponse:
    admin_id = admin.id
    if user_id == admin_id and payload.is_active is False:
        raise AppError(
            code="CANNOT_DEACTIVATE_SELF",
            message="管理员不能停用自己的账号。",
            status_code=409,
        )

    locked_admins = list(
        await session.scalars(
            select(User).where(User.role == ADMIN_ROLE).order_by(User.id).with_for_update()
        )
    )
    target = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if target is None:
        raise _user_not_found()

    loses_admin_access = (
        target.role == ADMIN_ROLE
        and target.is_active
        and (payload.role == "user" or payload.is_active is False)
    )
    active_admin_count = sum(user.is_active for user in locked_admins)
    if loses_admin_access and active_admin_count <= 1:
        raise AppError(
            code="LAST_ADMIN_REQUIRED",
            message="系统必须保留至少一个启用的管理员。",
            status_code=409,
        )

    should_revoke = (
        target.is_active
        and payload.is_active is False
        or target.role == ADMIN_ROLE
        and payload.role == "user"
    )
    if payload.role is not None:
        target.role = payload.role
    if payload.is_active is not None:
        target.is_active = payload.is_active

    if should_revoke:
        await AuthService(
            session=session,
            settings=settings,
        ).revoke_all_for_user_in_transaction(target.id)
    await session.commit()
    await session.refresh(target)
    return AdminUserResponse.model_validate(target)


@router.post("/{user_id}/reset-password", response_model=AdminUserResponse)
async def reset_password(
    user_id: UUID,
    payload: AdminPasswordReset,
    _admin: Annotated[User, Depends(require_admin)],
    session: Annotated[AsyncSession, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> AdminUserResponse:
    target = await session.scalar(select(User).where(User.id == user_id).with_for_update())
    if target is None:
        raise _user_not_found()
    target.password_hash = await run_in_threadpool(hash_password, payload.password)
    await AuthService(
        session=session,
        settings=settings,
    ).revoke_all_for_user_in_transaction(target.id)
    await session.commit()
    await session.refresh(target)
    return AdminUserResponse.model_validate(target)


def _username_exists() -> AppError:
    return AppError(
        code="USERNAME_ALREADY_EXISTS",
        message="用户名已存在。",
        status_code=409,
    )


def _user_not_found() -> AppError:
    return AppError(code="USER_NOT_FOUND", message="用户不存在。", status_code=404)

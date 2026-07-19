from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import get_current_user
from app.audit.service import add_audit_event, record_denied_audit_event
from app.authorization.service import get_owned_knowledge_base
from app.authorization.support_service import get_database_now
from app.core.exceptions import AppError
from app.db.models import ADMIN_ROLE, KnowledgeBase, SupportAccessGrant, User
from app.db.session import get_session

router = APIRouter(tags=["support-grants"])


class SupportGrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    admin_user_id: UUID
    expires_in_minutes: int = Field(default=30, ge=5, le=120)


class SupportGrantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    knowledge_base_id: UUID
    admin_user_id: UUID
    access_level: str
    expires_at: datetime
    revoked_at: datetime | None
    created_at: datetime
    last_used_at: datetime | None


class SupportAdministratorResponse(BaseModel):
    id: UUID
    username: str


@router.get("/api/v1/support-administrators", response_model=list[SupportAdministratorResponse])
async def list_support_administrators(
    session: Annotated[AsyncSession, Depends(get_session)],
    _current_user: Annotated[User, Depends(get_current_user)],
) -> list[SupportAdministratorResponse]:
    administrators = await session.scalars(
        select(User)
        .where(User.role == ADMIN_ROLE, User.is_active.is_(True))
        .order_by(User.username, User.id)
    )
    return [
        SupportAdministratorResponse(id=administrator.id, username=administrator.username)
        for administrator in administrators
    ]


async def _audit_management_denial(
    *,
    current_user: User,
    action: str,
    resource_type: str,
    resource_id: UUID,
    reason: str,
    result: str = "denied",
) -> None:
    await record_denied_audit_event(
        actor_user_id=current_user.id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        result=result,
        security_summary={"access_level": "read_only", "reason": reason},
    )


@router.post(
    "/api/v1/knowledge-bases/{knowledge_base_id}/support-grants",
    response_model=SupportGrantResponse,
    status_code=201,
)
async def create_support_grant(
    knowledge_base_id: UUID,
    payload: SupportGrantCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> SupportGrantResponse:
    try:
        knowledge_base = await get_owned_knowledge_base(
            session,
            current_user,
            knowledge_base_id,
            for_update=True,
        )
    except AppError:
        await _audit_management_denial(
            current_user=current_user,
            action="support_grant_create_denied",
            resource_type="knowledge_base",
            resource_id=knowledge_base_id,
            reason="owner_mismatch",
        )
        raise

    admin = await session.scalar(
        select(User).where(
            User.id == payload.admin_user_id,
            User.role == ADMIN_ROLE,
            User.is_active.is_(True),
        )
    )
    if admin is None:
        await _audit_management_denial(
            current_user=current_user,
            action="support_grant_create_denied",
            resource_type="knowledge_base",
            resource_id=knowledge_base_id,
            reason="admin_unavailable",
        )
        raise AppError(
            code="SUPPORT_ADMIN_NOT_FOUND",
            message="支持管理员不存在。",
            status_code=404,
        )

    now = await get_database_now(session)
    grant = SupportAccessGrant(
        knowledge_base_id=knowledge_base.id,
        owner_user_id=current_user.id,
        admin_user_id=admin.id,
        expires_at=now + timedelta(minutes=payload.expires_in_minutes),
    )
    try:
        async with session.begin_nested():
            session.add(grant)
            add_audit_event(
                session,
                actor_user_id=current_user.id,
                action="support_grant_created",
                resource_type="knowledge_base",
                resource_id=knowledge_base.id,
                result="success",
                security_summary={"access_level": "read_only"},
            )
            await session.flush()
    except IntegrityError as error:
        diagnostics = getattr(error.orig, "diag", None)
        if (
            getattr(error.orig, "sqlstate", None) != "23P01"
            or getattr(diagnostics, "constraint_name", None)
            != "ex_support_access_grants_unrevoked_period"
        ):
            raise
        await _audit_management_denial(
            current_user=current_user,
            action="support_grant_create_denied",
            resource_type="knowledge_base",
            resource_id=knowledge_base_id,
            reason="overlapping_grant",
            result="conflict",
        )
        raise AppError(
            code="SUPPORT_GRANT_CONFLICT",
            message="该管理员已存在时间重叠的支持授权。",
            status_code=409,
        ) from None

    await session.commit()
    return SupportGrantResponse.model_validate(grant)


@router.get(
    "/api/v1/knowledge-bases/{knowledge_base_id}/support-grants",
    response_model=list[SupportGrantResponse],
)
async def list_support_grants(
    knowledge_base_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[SupportGrantResponse]:
    try:
        await get_owned_knowledge_base(session, current_user, knowledge_base_id)
    except AppError:
        await _audit_management_denial(
            current_user=current_user,
            action="support_grant_list_denied",
            resource_type="knowledge_base",
            resource_id=knowledge_base_id,
            reason="owner_mismatch",
        )
        raise

    grants = (
        await session.scalars(
            select(SupportAccessGrant)
            .where(
                SupportAccessGrant.knowledge_base_id == knowledge_base_id,
                SupportAccessGrant.owner_user_id == current_user.id,
            )
            .order_by(SupportAccessGrant.created_at.desc(), SupportAccessGrant.id.desc())
        )
    ).all()
    return [SupportGrantResponse.model_validate(grant) for grant in grants]


@router.delete("/api/v1/support-grants/{grant_id}", status_code=204)
async def revoke_support_grant(
    grant_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    grant = await session.scalar(
        select(SupportAccessGrant)
        .join(KnowledgeBase, KnowledgeBase.id == SupportAccessGrant.knowledge_base_id)
        .where(
            SupportAccessGrant.id == grant_id,
            SupportAccessGrant.owner_user_id == current_user.id,
            SupportAccessGrant.revoked_at.is_(None),
            KnowledgeBase.owner_id == current_user.id,
            KnowledgeBase.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if grant is None:
        await _audit_management_denial(
            current_user=current_user,
            action="support_grant_revoke_denied",
            resource_type="support_grant",
            resource_id=grant_id,
            reason="grant_unavailable",
        )
        raise AppError(
            code="SUPPORT_GRANT_NOT_FOUND",
            message="支持授权不存在。",
            status_code=404,
        )

    grant.revoked_at = await get_database_now(session)
    add_audit_event(
        session,
        actor_user_id=current_user.id,
        action="support_grant_revoked",
        resource_type="support_grant",
        resource_id=grant.id,
        result="success",
        security_summary={"access_level": "read_only"},
    )
    await session.commit()
    return Response(status_code=204)

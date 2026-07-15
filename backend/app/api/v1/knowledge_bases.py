from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import get_current_user
from app.audit.service import record_denied_audit_event
from app.core.config import get_settings
from app.core.exceptions import AppError
from app.db.models import KnowledgeBase, User
from app.db.session import get_session
from app.lifecycle.service import (
    request_purge_knowledge_base,
    restore_knowledge_base,
    soft_delete_knowledge_base,
)

router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["knowledge-bases"])


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class KnowledgeBaseResponse(BaseModel):
    id: str
    name: str
    description: str | None
    owner_id: str
    owner_username: str


class PurgeJobResponse(BaseModel):
    job_id: UUID
    status: str


@router.post("", response_model=KnowledgeBaseResponse, status_code=201)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> KnowledgeBaseResponse:
    knowledge_base = KnowledgeBase(
        name=payload.name,
        description=payload.description,
        owner_id=current_user.id,
    )
    session.add(knowledge_base)
    await session.commit()
    return KnowledgeBaseResponse(
        id=str(knowledge_base.id),
        name=knowledge_base.name,
        description=knowledge_base.description,
        owner_id=str(knowledge_base.owner_id),
        owner_username=current_user.username,
    )


@router.get("", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[KnowledgeBaseResponse]:
    statement = (
        select(KnowledgeBase)
        .where(
            KnowledgeBase.owner_id == current_user.id,
            KnowledgeBase.deleted_at.is_(None),
        )
        .order_by(KnowledgeBase.created_at)
    )
    knowledge_bases = (await session.scalars(statement)).all()
    return [
        KnowledgeBaseResponse(
            id=str(knowledge_base.id),
            name=knowledge_base.name,
            description=knowledge_base.description,
            owner_id=str(knowledge_base.owner_id),
            owner_username=current_user.username,
        )
        for knowledge_base in knowledge_bases
    ]


async def _record_denial(
    session: AsyncSession,
    current_user: User,
    knowledge_base_id: UUID,
    action: str,
    error: AppError,
) -> None:
    actor_user_id = current_user.id
    await session.rollback()
    await record_denied_audit_event(
        actor_user_id=actor_user_id,
        action=action,
        resource_type="knowledge_base",
        resource_id=knowledge_base_id,
        security_summary={"reason": error.code},
    )


@router.delete("/{knowledge_base_id}", status_code=204)
async def delete_knowledge_base(
    knowledge_base_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    try:
        await soft_delete_knowledge_base(
            session,
            owner_user_id=current_user.id,
            knowledge_base_id=knowledge_base_id,
            retention_days=get_settings().trash_retention_days,
        )
        await session.commit()
    except AppError as error:
        await _record_denial(
            session, current_user, knowledge_base_id, "knowledge_base.delete", error
        )
        raise
    return Response(status_code=204)


@router.post("/{knowledge_base_id}/restore", status_code=204)
async def restore_deleted_knowledge_base(
    knowledge_base_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> Response:
    try:
        await restore_knowledge_base(
            session,
            owner_user_id=current_user.id,
            knowledge_base_id=knowledge_base_id,
        )
        await session.commit()
    except AppError as error:
        await _record_denial(
            session, current_user, knowledge_base_id, "knowledge_base.restore", error
        )
        raise
    return Response(status_code=204)


@router.delete("/{knowledge_base_id}/purge", response_model=PurgeJobResponse, status_code=202)
async def purge_deleted_knowledge_base(
    knowledge_base_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> PurgeJobResponse:
    try:
        job = await request_purge_knowledge_base(
            session,
            owner_user_id=current_user.id,
            knowledge_base_id=knowledge_base_id,
            max_attempts=get_settings().job_max_attempts,
        )
        await session.commit()
    except AppError as error:
        await _record_denial(
            session,
            current_user,
            knowledge_base_id,
            "knowledge_base.purge_request",
            error,
        )
        raise
    return PurgeJobResponse(job_id=job.id, status=job.status)

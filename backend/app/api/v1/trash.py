from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import get_current_user
from app.core.config import get_settings
from app.db.models import Document, KnowledgeBase, User
from app.db.session import get_session
from app.lifecycle.service import effective_purge_after

router = APIRouter(prefix="/api/v1/trash", tags=["trash"])


class TrashKnowledgeBase(BaseModel):
    id: UUID
    name: str
    deleted_at: datetime
    purge_after: datetime


class TrashDocument(BaseModel):
    id: UUID
    knowledge_base_id: UUID
    file_name: str
    deleted_at: datetime
    purge_after: datetime


class TrashResponse(BaseModel):
    knowledge_bases: list[TrashKnowledgeBase]
    documents: list[TrashDocument]


@router.get("", response_model=TrashResponse)
async def list_trash(
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TrashResponse:
    now = await session.scalar(select(func.clock_timestamp()))
    assert now is not None
    retention_days = get_settings().trash_retention_days
    knowledge_bases = (
        await session.scalars(
            select(KnowledgeBase)
            .where(
                KnowledgeBase.owner_id == current_user.id,
                KnowledgeBase.deleted_at.is_not(None),
            )
            .order_by(KnowledgeBase.deleted_at.desc(), KnowledgeBase.id)
        )
    ).all()
    documents = (
        await session.scalars(
            select(Document)
            .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
            .where(
                KnowledgeBase.owner_id == current_user.id,
                Document.deleted_at.is_not(None),
            )
            .order_by(Document.deleted_at.desc(), Document.id)
        )
    ).all()
    knowledge_base_deadlines = {
        item.id: effective_purge_after(item.deleted_at, item.purge_after, retention_days)
        for item in knowledge_bases
    }
    document_deadlines = {
        item.id: effective_purge_after(item.deleted_at, item.purge_after, retention_days)
        for item in documents
    }
    return TrashResponse(
        knowledge_bases=[
            TrashKnowledgeBase(
                id=item.id,
                name=item.name,
                deleted_at=item.deleted_at,
                purge_after=knowledge_base_deadlines[item.id],
            )
            for item in knowledge_bases
            if item.deleted_at is not None
            and knowledge_base_deadlines[item.id] is not None
            and knowledge_base_deadlines[item.id] > now
        ],
        documents=[
            TrashDocument(
                id=item.id,
                knowledge_base_id=item.knowledge_base_id,
                file_name=item.original_file_name,
                deleted_at=item.deleted_at,
                purge_after=document_deadlines[item.id],
            )
            for item in documents
            if item.deleted_at is not None
            and document_deadlines[item.id] is not None
            and document_deadlines[item.id] > now
        ],
    )

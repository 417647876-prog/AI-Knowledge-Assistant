from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models.knowledge_base import KnowledgeBase
from app.db.session import get_session

router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["knowledge-bases"])


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class KnowledgeBaseResponse(BaseModel):
    id: str
    name: str
    description: str | None


@router.post("", response_model=KnowledgeBaseResponse, status_code=201)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate, session: Annotated[AsyncSession, Depends(get_session)]
) -> KnowledgeBaseResponse:
    knowledge_base = KnowledgeBase(name=payload.name, description=payload.description)
    session.add(knowledge_base)
    await session.commit()
    return KnowledgeBaseResponse(
        id=str(knowledge_base.id), name=knowledge_base.name, description=knowledge_base.description
    )


@router.get("", response_model=list[KnowledgeBaseResponse])
async def list_knowledge_bases(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[KnowledgeBaseResponse]:
    rows = (await session.scalars(select(KnowledgeBase).order_by(KnowledgeBase.created_at))).all()
    return [
        KnowledgeBaseResponse(id=str(row.id), name=row.name, description=row.description)
        for row in rows
    ]

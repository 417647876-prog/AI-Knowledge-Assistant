from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import get_current_user
from app.db.models import KnowledgeBase, User
from app.db.session import get_session

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

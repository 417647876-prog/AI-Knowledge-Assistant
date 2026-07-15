from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth_dependencies import get_current_user
from app.authorization.support_service import (
    get_supported_document,
    get_supported_knowledge_base,
)
from app.db.models import Document, User
from app.db.session import get_session

router = APIRouter(prefix="/api/v1/support", tags=["support-content"])


class SupportKnowledgeBaseResponse(BaseModel):
    id: UUID
    name: str
    description: str | None


class SupportDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    knowledge_base_id: UUID
    original_file_name: str
    content_type: str
    file_size: int
    status: str
    error_code: str | None
    error_message: str | None


class SupportDocumentListResponse(BaseModel):
    items: list[SupportDocumentResponse]


@router.get(
    "/knowledge-bases/{knowledge_base_id}",
    response_model=SupportKnowledgeBaseResponse,
)
async def get_support_knowledge_base(
    knowledge_base_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> SupportKnowledgeBaseResponse:
    knowledge_base = await get_supported_knowledge_base(session, current_user, knowledge_base_id)
    response = SupportKnowledgeBaseResponse(
        id=knowledge_base.id,
        name=knowledge_base.name,
        description=knowledge_base.description,
    )
    await session.commit()
    return response


@router.get(
    "/knowledge-bases/{knowledge_base_id}/documents",
    response_model=SupportDocumentListResponse,
)
async def list_support_documents(
    knowledge_base_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> SupportDocumentListResponse:
    await get_supported_knowledge_base(session, current_user, knowledge_base_id)
    documents = (
        await session.scalars(
            select(Document)
            .where(
                Document.knowledge_base_id == knowledge_base_id,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.created_at.desc(), Document.id.desc())
        )
    ).all()
    response = SupportDocumentListResponse(
        items=[SupportDocumentResponse.model_validate(document) for document in documents]
    )
    await session.commit()
    return response


@router.get("/documents/{document_id}", response_model=SupportDocumentResponse)
async def get_support_document(
    document_id: UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> SupportDocumentResponse:
    document = await get_supported_document(session, current_user, document_id)
    response = SupportDocumentResponse.model_validate(document)
    await session.commit()
    return response

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AppError
from app.db.models import ADMIN_ROLE, Document, KnowledgeBase, User


async def get_accessible_knowledge_base(
    session: AsyncSession,
    current_user: User,
    knowledge_base_id: UUID,
    *,
    for_update: bool = False,
) -> KnowledgeBase:
    statement = select(KnowledgeBase).where(KnowledgeBase.id == knowledge_base_id)
    if current_user.role != ADMIN_ROLE:
        statement = statement.where(KnowledgeBase.owner_id == current_user.id)
    if for_update:
        statement = statement.with_for_update()

    knowledge_base = await session.scalar(statement)
    if knowledge_base is None:
        raise AppError(
            code="KNOWLEDGE_BASE_NOT_FOUND",
            message="知识库不存在。",
            status_code=404,
        )
    return knowledge_base


async def get_accessible_document(
    session: AsyncSession,
    current_user: User,
    document_id: UUID,
    *,
    for_update: bool = False,
) -> Document:
    statement = (
        select(Document)
        .join(KnowledgeBase, KnowledgeBase.id == Document.knowledge_base_id)
        .where(Document.id == document_id)
    )
    if current_user.role != ADMIN_ROLE:
        statement = statement.where(KnowledgeBase.owner_id == current_user.id)
    if for_update:
        statement = statement.with_for_update()

    document = await session.scalar(statement)
    if document is None:
        raise AppError(
            code="DOCUMENT_NOT_FOUND",
            message="文档不存在。",
            status_code=404,
        )
    return document

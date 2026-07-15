from collections.abc import Iterable
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Document,
    DocumentChunk,
    DocumentJob,
    KnowledgeBase,
    SupportAccessGrant,
)


async def delete_owned_knowledge_bases(session: AsyncSession, owner_ids: Iterable[UUID]) -> None:
    """按业务依赖顺序清理测试知识库，不依赖生产库级联删除。"""
    owner_ids = tuple(owner_ids)
    if not owner_ids:
        return
    knowledge_base_ids = select(KnowledgeBase.id).where(KnowledgeBase.owner_id.in_(owner_ids))
    await session.execute(
        delete(DocumentChunk).where(DocumentChunk.knowledge_base_id.in_(knowledge_base_ids))
    )
    await session.execute(
        delete(DocumentJob).where(DocumentJob.knowledge_base_id.in_(knowledge_base_ids))
    )
    await session.execute(
        delete(SupportAccessGrant).where(
            SupportAccessGrant.knowledge_base_id.in_(knowledge_base_ids)
        )
    )
    await session.execute(
        delete(Document).where(Document.knowledge_base_id.in_(knowledge_base_ids))
    )
    await session.execute(delete(KnowledgeBase).where(KnowledgeBase.id.in_(knowledge_base_ids)))

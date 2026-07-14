import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from sqlalchemy import delete

from app.core.security import hash_password
from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.knowledge_base import KnowledgeBase
from app.db.models.user import USER_ROLE, User
from app.db.session import session_factory
from app.rag.retriever import VectorRetriever

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@pytest.fixture
async def knowledge_base_owner() -> AsyncIterator[User]:
    user = User(
        id=uuid4(),
        username=f"vector_retriever_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    async with session_factory.begin() as session:
        session.add(user)
    try:
        yield user
    finally:
        async with session_factory.begin() as session:
            await session.execute(delete(KnowledgeBase).where(KnowledgeBase.owner_id == user.id))
            await session.execute(delete(User).where(User.id == user.id))


def _vector(first: float, second: float = 0.0) -> list[float]:
    return [first, second, *([0.0] * 510)]


async def _add_document(session, knowledge_base_id, name: str) -> Document:
    document = Document(
        knowledge_base_id=knowledge_base_id,
        original_file_name=name,
        stored_file_name=f"{uuid4()}.txt",
        content_type="text/plain",
        file_extension=".txt",
        file_size=10,
        file_hash=uuid4().hex * 2,
        status="ready",
    )
    session.add(document)
    await session.flush()
    return document


@pytest.mark.asyncio
async def test_search_orders_filters_limits_and_isolates_knowledge_base(
    knowledge_base_owner: User,
) -> None:
    async with session_factory() as session:
        target = KnowledgeBase(name=f"目标知识库-{uuid4()}", owner_id=knowledge_base_owner.id)
        other = KnowledgeBase(name=f"其他知识库-{uuid4()}", owner_id=knowledge_base_owner.id)
        session.add_all([target, other])
        await session.flush()
        target_document = await _add_document(session, target.id, "员工手册.txt")
        other_document = await _add_document(session, other.id, "其他资料.txt")
        session.add_all(
            [
                DocumentChunk(
                    document_id=target_document.id,
                    knowledge_base_id=target.id,
                    chunk_index=0,
                    content="完全相关",
                    content_hash="a" * 64,
                    embedding=_vector(1.0),
                ),
                DocumentChunk(
                    document_id=target_document.id,
                    knowledge_base_id=target.id,
                    chunk_index=1,
                    content="部分相关",
                    content_hash="b" * 64,
                    embedding=_vector(0.8, 0.6),
                ),
                DocumentChunk(
                    document_id=target_document.id,
                    knowledge_base_id=target.id,
                    chunk_index=2,
                    content="不相关",
                    content_hash="c" * 64,
                    embedding=_vector(0.0, 1.0),
                ),
                DocumentChunk(
                    document_id=other_document.id,
                    knowledge_base_id=other.id,
                    chunk_index=0,
                    content="其他知识库的高分内容",
                    content_hash="d" * 64,
                    embedding=_vector(1.0),
                ),
            ]
        )
        await session.flush()

        results = await VectorRetriever(session).search(
            knowledge_base_id=target.id,
            query_embedding=_vector(1.0),
            top_k=2,
            score_threshold=0.5,
        )

        await session.rollback()

    assert [item.content for item in results] == ["完全相关", "部分相关"]
    assert results[0].relevance_score == pytest.approx(1.0)
    assert results[1].relevance_score == pytest.approx(0.8)
    assert all(item.file_name == "员工手册.txt" for item in results)

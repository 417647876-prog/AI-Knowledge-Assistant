import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete

from app.core.security import hash_password
from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.knowledge_base import KnowledgeBase
from app.db.models.user import USER_ROLE, User
from app.db.session import session_factory
from app.knowledge.search_tokens import build_search_text
from app.rag.keyword_retriever import KeywordRetriever

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
        username=f"keyword_retriever_{uuid4().hex}",
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


async def _add_document(session, knowledge_base_id: UUID, name: str) -> Document:
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


def _chunk(
    *, chunk_id: UUID, document: Document, knowledge_base_id: UUID, content: str
) -> DocumentChunk:
    return DocumentChunk(
        id=chunk_id,
        document_id=document.id,
        knowledge_base_id=knowledge_base_id,
        chunk_index=0,
        content=content,
        content_hash=uuid4().hex * 2,
        embedding=[0.0] * 512,
        search_text=build_search_text(content),
    )


@pytest.mark.asyncio
async def test_search_matches_exact_code_and_isolates_knowledge_base(
    knowledge_base_owner: User,
) -> None:
    async with session_factory() as session:
        target = KnowledgeBase(name=f"目标关键词库-{uuid4()}", owner_id=knowledge_base_owner.id)
        other = KnowledgeBase(name=f"其他关键词库-{uuid4()}", owner_id=knowledge_base_owner.id)
        session.add_all([target, other])
        await session.flush()
        target_document = await _add_document(session, target.id, "员工手册.txt")
        other_document = await _add_document(session, other.id, "其他资料.txt")
        exact = _chunk(
            chunk_id=UUID(int=2),
            document=target_document,
            knowledge_base_id=target.id,
            content="VPN2026 账号申请流程",
        )
        session.add_all(
            [
                _chunk(
                    chunk_id=UUID(int=1),
                    document=target_document,
                    knowledge_base_id=target.id,
                    content="VPN 2026 分开记录",
                ),
                exact,
                _chunk(
                    chunk_id=UUID(int=3),
                    document=other_document,
                    knowledge_base_id=other.id,
                    content="VPN2026 其他知识库内容",
                ),
            ]
        )
        await session.flush()

        results = await KeywordRetriever(session).search(
            knowledge_base_id=target.id,
            query="VPN2026",
            query_embedding=[],
            top_k=10,
            score_threshold=0.99,
        )

        await session.rollback()

    assert [item.chunk_id for item in results] == [exact.id]
    assert results[0].file_name == "员工手册.txt"


@pytest.mark.asyncio
async def test_search_ranks_stably_and_applies_top_k(knowledge_base_owner: User) -> None:
    async with session_factory() as session:
        knowledge_base = KnowledgeBase(
            name=f"关键词排序库-{uuid4()}", owner_id=knowledge_base_owner.id
        )
        session.add(knowledge_base)
        await session.flush()
        document = await _add_document(session, knowledge_base.id, "VPN制度.txt")
        shared = _chunk(
            chunk_id=UUID(int=14),
            document=document,
            knowledge_base_id=knowledge_base.id,
            content="VPN2026 账号",
        )
        code_only = _chunk(
            chunk_id=UUID(int=12),
            document=document,
            knowledge_base_id=knowledge_base.id,
            content="VPN2026",
        )
        account_only = _chunk(
            chunk_id=UUID(int=13),
            document=document,
            knowledge_base_id=knowledge_base.id,
            content="账号",
        )
        session.add_all([account_only, shared, code_only])
        await session.flush()

        results = await KeywordRetriever(session).search(
            knowledge_base_id=knowledge_base.id,
            query="VPN2026 账号",
            query_embedding=[1.0],
            top_k=2,
            score_threshold=1.0,
        )

        await session.rollback()

    assert [item.chunk_id for item in results] == [shared.id, code_only.id]
    assert results[0].relevance_score > results[1].relevance_score


@pytest.mark.asyncio
async def test_search_returns_empty_for_query_without_tokens() -> None:
    async with session_factory() as session:
        results = await KeywordRetriever(session).search(
            knowledge_base_id=uuid4(),
            query="！？……",
            query_embedding=[],
            top_k=5,
            score_threshold=0.0,
        )

    assert results == []


@pytest.mark.asyncio
async def test_search_filters_candidates_with_too_little_query_token_coverage(
    knowledge_base_owner: User,
) -> None:
    async with session_factory() as session:
        knowledge_base = KnowledgeBase(
            name=f"关键词覆盖率库-{uuid4()}", owner_id=knowledge_base_owner.id
        )
        session.add(knowledge_base)
        await session.flush()
        document = await _add_document(session, knowledge_base.id, "差旅制度.txt")
        strong_match = _chunk(
            chunk_id=UUID(int=21),
            document=document,
            knowledge_base_id=knowledge_base.id,
            content="差旅住宿报销标准为每晚 500 元",
        )
        weak_match = _chunk(
            chunk_id=UUID(int=22),
            document=document,
            knowledge_base_id=knowledge_base.id,
            content="员工必须通过公司 VPN 访问内部系统",
        )
        session.add_all([strong_match, weak_match])
        await session.flush()

        results = await KeywordRetriever(session).search(
            knowledge_base_id=knowledge_base.id,
            query="公司差旅住宿报销标准是多少？",
            query_embedding=[],
            top_k=5,
            score_threshold=0.55,
        )

        await session.rollback()

    assert [item.chunk_id for item in results] == [strong_match.id]

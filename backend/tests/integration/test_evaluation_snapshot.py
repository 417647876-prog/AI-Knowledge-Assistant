import os
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select

from app.core.security import hash_password
from app.db.models.document import Document
from app.db.models.document_chunk import DocumentChunk
from app.db.models.knowledge_base import KnowledgeBase
from app.db.models.user import USER_ROLE, User
from app.db.session import session_factory
from app.evaluation import snapshot
from tests.database_cleanup import delete_owned_knowledge_bases

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


@pytest.fixture
async def snapshot_owner() -> AsyncIterator[User]:
    user = User(
        id=uuid4(),
        username=f"snapshot_{uuid4().hex}",
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
            await delete_owned_knowledge_bases(session, [user.id])
            await session.execute(delete(User).where(User.id == user.id))


def _vector(value: float) -> list[float]:
    return [value, *([0.0] * 511)]


async def _add_document_with_chunk(
    session,
    knowledge_base_id: UUID,
    uploader_id: UUID,
    name: str,
    content: str,
    embedding_value: float,
) -> DocumentChunk:
    document = Document(
        knowledge_base_id=knowledge_base_id,
        uploaded_by_user_id=uploader_id,
        original_file_name=name,
        stored_file_name=f"{uuid4()}.txt",
        content_type="text/plain",
        file_extension=".txt",
        file_size=len(content.encode("utf-8")),
        file_hash=uuid4().hex * 2,
        status="completed",
    )
    session.add(document)
    await session.flush()
    chunk = DocumentChunk(
        document_id=document.id,
        knowledge_base_id=knowledge_base_id,
        chunk_index=0,
        content=content,
        content_hash=uuid4().hex * 2,
        page_number=1,
        section_title="测试章节",
        start_index=0,
        extra_metadata={"source": "integration"},
        embedding=_vector(embedding_value),
        search_text=content,
    )
    session.add(chunk)
    await session.flush()
    return chunk


@pytest.mark.asyncio
async def test_snapshot_changes_only_for_target_knowledge_base(
    snapshot_owner: User,
) -> None:
    async with session_factory.begin() as session:
        target = KnowledgeBase(name=f"快照目标-{uuid4()}", owner_id=snapshot_owner.id)
        other = KnowledgeBase(name=f"快照隔离-{uuid4()}", owner_id=snapshot_owner.id)
        session.add_all([target, other])
        await session.flush()
        target_chunk = await _add_document_with_chunk(
            session,
            target.id,
            snapshot_owner.id,
            "目标资料.txt",
            "目标知识库正文",
            0.1,
        )
        other_chunk = await _add_document_with_chunk(
            session,
            other.id,
            snapshot_owner.id,
            "其他资料.txt",
            "其他知识库正文",
            0.2,
        )
        target_id = target.id
        target_chunk_id = target_chunk.id
        other_chunk_id = other_chunk.id

    async with session_factory() as session:
        before = await snapshot.compute_knowledge_base_snapshot(session, target_id)

    async with session_factory.begin() as session:
        stored_other_chunk = await session.scalar(
            select(DocumentChunk).where(DocumentChunk.id == other_chunk_id)
        )
        assert stored_other_chunk is not None
        stored_other_chunk.content = "其他知识库发生变化"

    async with session_factory() as session:
        after_other_change = await snapshot.compute_knowledge_base_snapshot(session, target_id)

    assert after_other_change == before

    async with session_factory.begin() as session:
        stored_target_chunk = await session.scalar(
            select(DocumentChunk).where(DocumentChunk.id == target_chunk_id)
        )
        assert stored_target_chunk is not None
        stored_target_chunk.content = "目标知识库发生变化"

    async with session_factory() as session:
        after_target_change = await snapshot.compute_knowledge_base_snapshot(session, target_id)

    assert after_target_change.snapshot_sha256 != before.snapshot_sha256

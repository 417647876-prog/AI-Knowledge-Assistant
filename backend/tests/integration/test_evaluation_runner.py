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
from app.evaluation.runner import evaluate_cases
from app.evaluation.schemas import EvaluationCase, ExpectedSource
from app.rag.retriever import VectorRetriever
from app.rag.schemas import QuestionAnswer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


class FixedEmbeddingProvider:
    async def embed_query(self, text: str) -> list[float]:
        return _vector(1.0)


class FixedAnswerer:
    async def answer_case(self, **kwargs) -> QuestionAnswer:
        return QuestionAnswer(answer="测试答案", citations=[], retrieved_chunk_count=1)


@pytest.fixture
async def knowledge_base_owner() -> AsyncIterator[User]:
    user = User(
        id=uuid4(),
        username=f"evaluation_runner_{uuid4().hex}",
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


@pytest.mark.asyncio
async def test_evaluation_runner_excludes_higher_scoring_other_knowledge_base_chunk(
    knowledge_base_owner: User,
) -> None:
    async with session_factory() as session:
        target = KnowledgeBase(name=f"评估目标-{uuid4()}", owner_id=knowledge_base_owner.id)
        other = KnowledgeBase(name=f"评估隔离-{uuid4()}", owner_id=knowledge_base_owner.id)
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
                    content="试用期为三个月。",
                    content_hash="a" * 64,
                    embedding=_vector(0.8, 0.6),
                ),
                DocumentChunk(
                    document_id=other_document.id,
                    knowledge_base_id=other.id,
                    chunk_index=0,
                    content="其他知识库的高分片段。",
                    content_hash="b" * 64,
                    embedding=_vector(1.0),
                ),
            ]
        )
        await session.flush()

        report = await evaluate_cases(
            cases=[
                EvaluationCase(
                    id="keyword-001",
                    category="keyword",
                    question="试用期多久？",
                    expected_sources=[ExpectedSource(file_name="员工手册.txt", contains="三个月")],
                )
            ],
            knowledge_base_id=target.id,
            embedding_provider=FixedEmbeddingProvider(),
            retriever=VectorRetriever(session),
            answerer=FixedAnswerer(),
            top_k=5,
            score_threshold=0.5,
        )

        await session.rollback()

    assert report.cases[0].retrieved_files == ["员工手册.txt"]
    assert report.recall_at_5 == 1.0

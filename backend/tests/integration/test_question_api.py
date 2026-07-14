import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete

from app.api.v1.questions import get_rag_service
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db.models import USER_ROLE, KnowledgeBase, RefreshSession, User
from app.db.session import session_factory
from app.main import create_app
from app.rag.schemas import Citation, QuestionAnswer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_DATABASE_TESTS") != "1",
        reason="设置 RUN_DATABASE_TESTS=1 后运行 PostgreSQL 集成测试",
    ),
]


class StubRagService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str, int]] = []
        self.document_id = uuid4()

    async def answer(self, knowledge_base_id, question: str, top_k: int) -> QuestionAnswer:
        self.calls.append((knowledge_base_id, question, top_k))
        return QuestionAnswer(
            answer="员工有五天年假。[1]",
            citations=[
                Citation(
                    citation_id=1,
                    document_id=self.document_id,
                    file_name="员工手册.pdf",
                    page_number=12,
                    content="员工有五天年假。",
                    relevance_score=0.91,
                )
            ],
            retrieved_chunk_count=1,
        )


@dataclass
class QuestionContext:
    client: httpx.AsyncClient
    knowledge_base_id: UUID
    service: StubRagService


@pytest.fixture
async def question_context() -> AsyncIterator[QuestionContext]:
    user = User(
        id=uuid4(),
        username=f"question_{uuid4().hex}",
        password_hash=hash_password("correct horse battery"),
        role=USER_ROLE,
        is_active=True,
    )
    async with session_factory.begin() as session:
        session.add(user)
        await session.flush()
        knowledge_base = KnowledgeBase(
            name=f"问答接口测试-{uuid4()}",
            owner_id=user.id,
        )
        session.add(knowledge_base)
    token = create_access_token(user_id=user.id, role=user.role, settings=get_settings())
    service = StubRagService()
    app = create_app()
    app.dependency_overrides[get_rag_service] = lambda: service
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {token}"},
    ) as client:
        try:
            yield QuestionContext(client, knowledge_base.id, service)
        finally:
            async with session_factory.begin() as session:
                await session.execute(
                    delete(KnowledgeBase).where(KnowledgeBase.owner_id == user.id)
                )
                await session.execute(
                    delete(RefreshSession).where(RefreshSession.user_id == user.id)
                )
                await session.execute(delete(User).where(User.id == user.id))


@pytest.mark.asyncio
async def test_question_api_returns_answer_citations_and_request_id(
    question_context: QuestionContext,
) -> None:
    response = await question_context.client.post(
        f"/api/v1/knowledge-bases/{question_context.knowledge_base_id}/questions",
        json={"question": "年假有几天？", "top_k": 3},
        headers={"X-Request-ID": "question-test-id"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "员工有五天年假。[1]",
        "citations": [
            {
                "citation_id": 1,
                "document_id": str(question_context.service.document_id),
                "file_name": "员工手册.pdf",
                "content": "员工有五天年假。",
                "relevance_score": 0.91,
                "page_number": 12,
                "sheet_name": None,
                "row_start": None,
                "section_title": None,
            }
        ],
        "retrieved_chunk_count": 1,
        "request_id": "question-test-id",
    }
    assert question_context.service.calls == [
        (question_context.knowledge_base_id, "年假有几天？", 3)
    ]


@pytest.mark.asyncio
async def test_question_api_rejects_blank_question_and_oversized_top_k(
    question_context: QuestionContext,
) -> None:
    response = await question_context.client.post(
        f"/api/v1/knowledge-bases/{question_context.knowledge_base_id}/questions",
        json={"question": "   ", "top_k": 21},
    )

    assert response.status_code == 422

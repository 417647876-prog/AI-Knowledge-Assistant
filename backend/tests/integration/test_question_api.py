import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import delete

from app.ai.contracts import ConversationMessage
from app.api.v1.questions import get_rag_service
from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.db.models import USER_ROLE, KnowledgeBase, RefreshSession, User
from app.db.session import session_factory
from app.main import create_app
from app.rag.schemas import Citation, QuestionAnswer
from app.rag.streaming import StreamEvent

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
        self.stream_calls: list[tuple[object, str, int, object]] = []
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

    async def stream_answer(self, knowledge_base_id, question, top_k, history):
        self.stream_calls.append((knowledge_base_id, question, top_k, history))
        yield StreamEvent("rewrite", {"standalone_question": "独立问题", "elapsed_ms": 10})
        yield StreamEvent("retrieval", {"retrieved_chunk_count": 1, "elapsed_ms": 20})
        yield StreamEvent("token", {"delta": "答案。[1]"})
        yield StreamEvent(
            "done",
            {
                "citations": [],
                "retrieved_chunk_count": 1,
                "timings": {
                    "rewrite_ms": 10,
                    "retrieval_ms": 20,
                    "generation_ms": 30,
                    "total_ms": 60,
                },
            },
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


@pytest.mark.asyncio
async def test_stream_question_rejects_anonymous_request_as_http_error() -> None:
    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/knowledge-bases/{uuid4()}/questions/stream",
            json={"question": "匿名请求"},
        )

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


@pytest.mark.asyncio
async def test_stream_question_returns_sse_and_forwards_valid_history(
    question_context: QuestionContext,
) -> None:
    response = await question_context.client.post(
        f"/api/v1/knowledge-bases/{question_context.knowledge_base_id}/questions/stream",
        json={
            "question": "它呢？",
            "top_k": 3,
            "history": [
                {"role": "user", "content": "首问"},
                {"role": "assistant", "content": "首答"},
            ],
        },
        headers={"X-Request-ID": "stream-req-1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert "event: rewrite" in response.text
    assert '"request_id":"stream-req-1"' in response.text
    assert question_context.service.stream_calls == [
        (
            question_context.knowledge_base_id,
            "它呢？",
            3,
            [
                ConversationMessage(role="user", content="首问"),
                ConversationMessage(role="assistant", content="首答"),
            ],
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "history",
    [
        [{"role": "assistant", "content": "孤立回答"}],
        [{"role": "user", "content": "缺少回答"}],
        [{"role": "user", "content": "a" * 2001}, {"role": "assistant", "content": "回答"}],
        [{"role": "user", "content": "提问"}, {"role": "assistant", "content": "a" * 8001}],
        [
            message
            for _ in range(7)
            for message in (
                {"role": "user", "content": "提问"},
                {"role": "assistant", "content": "回答"},
            )
        ],
    ],
)
async def test_stream_question_rejects_invalid_history(
    question_context: QuestionContext, history: list[dict[str, str]]
) -> None:
    response = await question_context.client.post(
        f"/api/v1/knowledge-bases/{question_context.knowledge_base_id}/questions/stream",
        json={"question": "追问", "history": history},
    )

    assert response.status_code == 422

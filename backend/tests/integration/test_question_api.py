from uuid import uuid4

import httpx
import pytest

from app.api.v1.questions import get_rag_service
from app.main import create_app
from app.rag.schemas import Citation, QuestionAnswer


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


@pytest.mark.asyncio
async def test_question_api_returns_answer_citations_and_request_id() -> None:
    service = StubRagService()
    app = create_app()
    app.dependency_overrides[get_rag_service] = lambda: service
    knowledge_base_id = uuid4()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/questions",
            json={"question": "年假有几天？", "top_k": 3},
            headers={"X-Request-ID": "question-test-id"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "员工有五天年假。[1]",
        "citations": [
            {
                "citation_id": 1,
                "document_id": str(service.document_id),
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
    assert service.calls == [(knowledge_base_id, "年假有几天？", 3)]


@pytest.mark.asyncio
async def test_question_api_rejects_blank_question_and_oversized_top_k() -> None:
    app = create_app()
    app.dependency_overrides[get_rag_service] = lambda: StubRagService()
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            f"/api/v1/knowledge-bases/{uuid4()}/questions",
            json={"question": "   ", "top_k": 21},
        )

    assert response.status_code == 422

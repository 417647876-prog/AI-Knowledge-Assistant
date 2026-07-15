from uuid import uuid4

import httpx
import pytest

from app.ai.chat import FakeChatProvider, OpenAICompatibleChatProvider
from app.ai.embeddings import FakeEmbeddingProvider
from app.ai.rewrite import ChatQuestionRewriter, FakeQuestionRewriter
from app.api.v1.questions import get_question_rewriter, get_rag_service
from app.core.config import Settings
from app.rag.schemas import RetrievedChunk


class FakeSession:
    async def get(self, model: object, identity: object) -> object:
        return object()


def _chunk() -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_name="员工手册.pdf",
        content="入职满一年可享受五天年假。",
        relevance_score=0.92,
        page_number=12,
    )


@pytest.mark.asyncio
async def test_rag_service_factory_uses_fake_rewriter_and_keeps_answer_path_available() -> None:
    question_rewriter = await get_question_rewriter(
        settings=Settings(_env_file=None, embedding_provider="fake", chat_provider="fake"),
        chat_provider=FakeChatProvider(),
    )
    service = await get_rag_service(
        session=FakeSession(),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        chat_provider=FakeChatProvider(answer="员工可享受五天年假。[1]"),
        question_rewriter=question_rewriter,
        settings=Settings(_env_file=None, embedding_provider="fake", chat_provider="fake"),
    )

    async def search(**kwargs: object) -> list[RetrievedChunk]:
        return [_chunk()]

    service._retriever.search = search  # type: ignore[method-assign]

    result = await service.answer(uuid4(), "年假有几天？", 5)

    assert isinstance(service._question_rewriter, FakeQuestionRewriter)
    assert result.answer == "员工可享受五天年假。[1]"
    assert result.retrieved_chunk_count == 1


@pytest.mark.asyncio
async def test_rag_service_factory_wraps_real_chat_provider_for_question_rewriting() -> None:
    async with httpx.AsyncClient() as client:
        chat_provider = OpenAICompatibleChatProvider(
            client=client,
            base_url="https://chat.example/v1",
            api_key="test-key",
            model="chat-model",
        )
        settings = Settings(
            _env_file=None,
            embedding_provider="fake",
            chat_provider="deepseek",
            chat_api_key="test-key",
        )
        question_rewriter = await get_question_rewriter(
            settings=settings,
            chat_provider=chat_provider,
        )
        service = await get_rag_service(
            session=FakeSession(),
            embedding_provider=FakeEmbeddingProvider(dimensions=512),
            chat_provider=chat_provider,
            question_rewriter=question_rewriter,
            settings=settings,
        )

    assert isinstance(service._question_rewriter, ChatQuestionRewriter)
    assert service._question_rewriter._chat_provider is chat_provider

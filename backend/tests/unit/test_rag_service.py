from uuid import uuid4

import pytest

from app.ai.contracts import ChatCompletion, ChatStreamChunk, ConversationMessage
from app.ai.embeddings import FakeEmbeddingProvider
from app.core.exceptions import AppError
from app.core.request_context import reset_request_id, set_request_id
from app.rag.schemas import RetrievedChunk
from app.rag.service import RagService


class FakeSession:
    def __init__(self, knowledge_base: object | None) -> None:
        self.knowledge_base = knowledge_base

    async def get(self, model, identifier):
        return self.knowledge_base


class ScopedFakeSession:
    def __init__(self) -> None:
        self.statement = None

    async def scalar(self, statement):
        self.statement = statement
        return None


class StubRetriever:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, object]] = []

    async def search(self, **kwargs) -> list[RetrievedChunk]:
        self.calls.append(kwargs)
        return self.chunks


class StubReranker:
    def __init__(
        self,
        *,
        scores: list[object] | None = None,
        error: AppError | None = None,
    ) -> None:
        self.scores = scores or []
        self.error = error
        self.calls: list[tuple[str, list[str]]] = []

    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        self.calls.append((query, documents))
        if self.error is not None:
            raise self.error
        return self.scores  # type: ignore[return-value]


class CountingChatProvider:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.call_count = 0

    async def generate(self, system_prompt: str, user_prompt: str) -> ChatCompletion:
        self.call_count += 1
        return ChatCompletion(
            content=self.answer,
            usage=None,
            finish_reason="stop",
            provider_request_id="test-request",
        )


class RecordingRewriter:
    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[tuple[list[ConversationMessage], str]] = []

    async def rewrite(self, history: list[ConversationMessage], question: str) -> str:
        self.calls.append((history, question))
        return self.result


class FailingRewriter:
    def __init__(self, code: str = "QUESTION_REWRITE_ERROR") -> None:
        self.code = code

    async def rewrite(
        self,
        history: list[ConversationMessage],
        question: str,
    ) -> str:
        raise AppError(code=self.code, message="改写失败", status_code=502)


class StreamingCountingChatProvider(CountingChatProvider):
    def __init__(self, answer: str, tokens: list[str]) -> None:
        super().__init__(answer)
        self.tokens = tokens
        self.stream_closed = False

    async def stream(self, system_prompt: str, user_prompt: str):
        try:
            for token in self.tokens:
                yield ChatStreamChunk(kind="token", delta=token)
            yield ChatStreamChunk(
                kind="done",
                finish_reason="stop",
                provider_request_id="test-request",
            )
        finally:
            self.stream_closed = True


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
async def test_answer_retrieves_generates_and_maps_real_citations() -> None:
    chunk = _chunk()
    retriever = StubRetriever([chunk])
    chat = CountingChatProvider("员工可享受五天年假。[1][99]")
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=retriever,
        chat_provider=chat,
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=None,
        candidate_k=20,
        reranker_allow_fallback=True,
    )
    knowledge_base_id = uuid4()

    result = await service.answer(
        knowledge_base_id=knowledge_base_id,
        question="  年假有几天？  ",
        top_k=5,
    )

    assert result.answer == "员工可享受五天年假。[1][99]"
    assert result.retrieved_chunk_count == 1
    assert [item.citation_id for item in result.citations] == [1]
    assert retriever.calls[0]["knowledge_base_id"] == knowledge_base_id
    assert retriever.calls[0]["query"] == "年假有几天？"
    assert retriever.calls[0]["score_threshold"] == 0.55
    assert chat.call_count == 1


@pytest.mark.asyncio
async def test_answer_refuses_without_chunks_and_does_not_call_chat() -> None:
    chat = CountingChatProvider("不应该被调用")
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever([]),
        chat_provider=chat,
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=None,
        candidate_k=20,
        reranker_allow_fallback=True,
    )

    result = await service.answer(uuid4(), "不存在的制度", 5)

    assert result.answer == "未找到足够依据，无法根据当前知识库回答该问题。"
    assert result.citations == []
    assert result.retrieved_chunk_count == 0
    assert chat.call_count == 0


@pytest.mark.asyncio
async def test_answer_rejects_missing_knowledge_base() -> None:
    retriever = StubRetriever([])
    service = RagService(
        session=FakeSession(None),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=retriever,
        chat_provider=CountingChatProvider("unused"),
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=None,
        candidate_k=20,
        reranker_allow_fallback=True,
    )

    with pytest.raises(AppError) as error:
        await service.answer(uuid4(), "问题", 5)

    assert error.value.code == "KNOWLEDGE_BASE_NOT_FOUND"
    assert retriever.calls == []


@pytest.mark.asyncio
async def test_api_scoped_rag_service_rechecks_owner_and_active_knowledge_base() -> None:
    session = ScopedFakeSession()
    retriever = StubRetriever([])
    owner_user_id = uuid4()
    service = RagService(
        session=session,
        owner_user_id=owner_user_id,
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=retriever,
        chat_provider=CountingChatProvider("unused"),
        question_rewriter=RecordingRewriter("unused"),
        score_threshold=0.55,
    )

    with pytest.raises(AppError) as error:
        await service.answer(uuid4(), "private question", 5)

    assert error.value.code == "KNOWLEDGE_BASE_NOT_FOUND"
    assert retriever.calls == []
    statement = str(session.statement)
    assert "knowledge_bases.owner_id" in statement
    assert "knowledge_bases.deleted_at IS NULL" in statement


@pytest.mark.asyncio
async def test_stream_rewrites_retrieves_generates_citations_and_timings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = _chunk()
    rewriter = RecordingRewriter("向量检索有什么缺点？")
    chat = StreamingCountingChatProvider("unused", ["答案 [", "1]"])
    retriever = StubRetriever([chunk])
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=retriever,
        chat_provider=chat,
        question_rewriter=rewriter,
        score_threshold=0.55,
        reranker=None,
        candidate_k=20,
        reranker_allow_fallback=True,
    )
    history = [
        ConversationMessage(role="user", content="介绍向量检索"),
        ConversationMessage(role="assistant", content="它使用向量相似度"),
    ]
    prompt_questions: list[str] = []

    def recording_prompt(question: str, chunks: list[RetrievedChunk]) -> tuple[str, str]:
        prompt_questions.append(question)
        return "system", "user"

    monkeypatch.setattr("app.rag.service.build_rag_prompt", recording_prompt)

    events = [item async for item in service.stream_answer(uuid4(), "它的缺点？", 5, history)]

    assert [item.event for item in events] == [
        "status",
        "rewrite",
        "status",
        "retrieval",
        "status",
        "token",
        "token",
        "citation",
        "done",
    ]
    assert events[1].data == {
        "standalone_question": "向量检索有什么缺点？",
        "elapsed_ms": events[1].data["elapsed_ms"],
        "used_fallback": False,
    }
    assert events[3].data["retrieved_chunk_count"] == 1
    assert events[-1].data["timings"].keys() == {
        "rewrite_ms",
        "retrieval_ms",
        "generation_ms",
        "total_ms",
    }
    assert rewriter.calls == [(history, "它的缺点？")]
    assert retriever.calls[0]["query"] == "向量检索有什么缺点？"
    assert prompt_questions == ["它的缺点？"]


@pytest.mark.asyncio
async def test_stream_with_history_skips_rewriter_for_complete_question() -> None:
    rewriter = RecordingRewriter("不应调用")
    retriever = StubRetriever([])
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=retriever,
        chat_provider=StreamingCountingChatProvider("", []),
        question_rewriter=rewriter,
        score_threshold=0.55,
    )
    history = [
        ConversationMessage(role="user", content="介绍年假制度"),
        ConversationMessage(role="assistant", content="这是制度摘要"),
    ]
    question = "员工入职满一年有多少天带薪年假？"

    events = [item async for item in service.stream_answer(uuid4(), question, 5, history)]

    assert rewriter.calls == []
    assert retriever.calls[0]["query"] == question
    assert events[0].data == {
        "standalone_question": question,
        "elapsed_ms": 0,
        "used_fallback": False,
    }


@pytest.mark.asyncio
async def test_stream_falls_back_only_for_question_rewrite_error() -> None:
    retriever = StubRetriever([])
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=retriever,
        chat_provider=StreamingCountingChatProvider("", []),
        question_rewriter=FailingRewriter(),
        score_threshold=0.55,
    )
    history = [
        ConversationMessage(role="user", content="介绍向量检索"),
        ConversationMessage(role="assistant", content="它使用向量相似度"),
    ]

    events = [item async for item in service.stream_answer(uuid4(), "它有什么缺点？", 5, history)]
    rewrite_event = next(item for item in events if item.event == "rewrite")

    assert rewrite_event.data["standalone_question"] == "它有什么缺点？"
    assert rewrite_event.data["used_fallback"] is True
    assert retriever.calls[0]["query"] == "它有什么缺点？"


@pytest.mark.asyncio
async def test_stream_does_not_swallow_other_rewrite_errors() -> None:
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever([]),
        chat_provider=StreamingCountingChatProvider("", []),
        question_rewriter=FailingRewriter("OTHER_ERROR"),
        score_threshold=0.55,
    )
    history = [
        ConversationMessage(role="user", content="介绍向量检索"),
        ConversationMessage(role="assistant", content="它使用向量相似度"),
    ]

    with pytest.raises(AppError) as captured:
        _ = [item async for item in service.stream_answer(uuid4(), "它有什么缺点？", 5, history)]

    assert captured.value.code == "OTHER_ERROR"


@pytest.mark.asyncio
async def test_answer_with_retrieval_question_separates_retrieval_and_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chunk = _chunk()
    retriever = StubRetriever([chunk])
    prompt_questions: list[str] = []

    def recording_prompt(question: str, chunks: list[RetrievedChunk]) -> tuple[str, str]:
        prompt_questions.append(question)
        return "system", "user"

    monkeypatch.setattr("app.rag.service.build_rag_prompt", recording_prompt)
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=retriever,
        chat_provider=CountingChatProvider("答案。[1]"),
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
    )

    result = await service.answer_with_retrieval_question(
        uuid4(),
        original_question="  它有什么缺点？  ",
        retrieval_question="向量检索有什么缺点？",
        top_k=5,
    )

    assert retriever.calls[0]["query"] == "向量检索有什么缺点？"
    assert prompt_questions == ["它有什么缺点？"]
    assert [item.document_id for item in result.citations] == [chunk.document_id]


@pytest.mark.asyncio
async def test_stream_without_history_skips_rewriter_and_uses_zero_ms() -> None:
    rewriter = RecordingRewriter("不应调用")
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever([]),
        chat_provider=StreamingCountingChatProvider("", []),
        question_rewriter=rewriter,
        score_threshold=0.55,
        reranker=None,
        candidate_k=20,
        reranker_allow_fallback=True,
    )

    events = [item async for item in service.stream_answer(uuid4(), "首问", 5, [])]

    assert events[0].event == "rewrite"
    assert events[0].data == {
        "standalone_question": "首问",
        "elapsed_ms": 0,
        "used_fallback": False,
    }
    assert "rewriting" not in [item.data.get("phase") for item in events]
    assert rewriter.calls == []
    assert [item.data.get("delta") for item in events if item.event == "token"] == [
        "未找到足够依据，无法根据当前知识库回答该问题。"
    ]


@pytest.mark.asyncio
async def test_closing_service_stream_closes_chat_stream() -> None:
    chat = StreamingCountingChatProvider("unused", ["一", "二"])
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever([_chunk()]),
        chat_provider=chat,
        question_rewriter=RecordingRewriter("问题"),
        score_threshold=0.55,
        reranker=None,
        candidate_k=20,
        reranker_allow_fallback=True,
    )
    stream = service.stream_answer(uuid4(), "问题", 5, [])
    while (await anext(stream)).event != "token":
        pass

    await stream.aclose()

    assert chat.stream_closed is True


@pytest.mark.asyncio
async def test_disabled_reranker_keeps_requested_top_k_and_original_order() -> None:
    chunks = [_chunk(), _chunk()]
    retriever = StubRetriever(chunks)
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=retriever,
        chat_provider=CountingChatProvider("答案。[1][2]"),
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=None,
        candidate_k=20,
        reranker_allow_fallback=True,
    )

    result = await service.answer(uuid4(), "  年假有几天？  ", 2)

    assert retriever.calls[0]["top_k"] == 2
    assert [item.document_id for item in result.citations] == [
        chunks[0].document_id,
        chunks[1].document_id,
    ]
    assert result.retrieved_chunk_count == 2


@pytest.mark.asyncio
async def test_enabled_reranker_retrieves_candidates_reorders_and_limits_top_k() -> None:
    chunks = [_chunk(), _chunk(), _chunk()]
    retriever = StubRetriever(chunks)
    reranker = StubReranker(scores=[0.1, 0.9, 0.3])
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=retriever,
        chat_provider=CountingChatProvider("答案。[1][2]"),
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=reranker,
        candidate_k=3,
        reranker_allow_fallback=False,
    )

    result = await service.answer(uuid4(), "  年假有几天？  ", 2)

    assert retriever.calls[0]["top_k"] == 3
    assert reranker.calls == [("年假有几天？", [chunk.content for chunk in chunks])]
    assert [item.document_id for item in result.citations] == [
        chunks[1].document_id,
        chunks[2].document_id,
    ]
    assert [item.relevance_score for item in result.citations] == [0.9, 0.3]
    assert result.retrieved_chunk_count == 2


@pytest.mark.asyncio
async def test_reranker_acceptance_gate_keeps_only_qualified_chunks() -> None:
    chunks = [_chunk(), _chunk(), _chunk()]
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever(chunks),
        chat_provider=CountingChatProvider("答案。[1]"),
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=StubReranker(scores=[0.8, 0.49, -1.0]),
        candidate_k=3,
        reranker_allow_fallback=False,
        reranker_min_score=0.5,
    )

    answer, final_chunks, _ = await service.answer_with_retrieval(uuid4(), "年假", 3)

    assert [item.relevance_score for item in final_chunks] == [0.8]
    assert answer.retrieved_chunk_count == 1


@pytest.mark.asyncio
async def test_successful_low_score_rerank_refuses_without_fallback_or_chat() -> None:
    chunks = [_chunk(), _chunk()]
    chat = CountingChatProvider("不应该被调用")
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever(chunks),
        chat_provider=chat,
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=StubReranker(scores=[-2.0, -3.0]),
        candidate_k=2,
        reranker_allow_fallback=True,
        reranker_min_score=0.0,
    )

    answer, final_chunks, _ = await service.answer_with_retrieval(uuid4(), "无关问题", 2)

    assert final_chunks == []
    assert answer.answer == "未找到足够依据，无法根据当前知识库回答该问题。"
    assert answer.citations == []
    assert answer.retrieved_chunk_count == 0
    assert chat.call_count == 0


@pytest.mark.asyncio
async def test_strict_reranker_failure_is_propagated() -> None:
    error = AppError(
        code="RERANKER_PROVIDER_ERROR",
        message="重排序失败。",
        status_code=502,
    )
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever([_chunk()]),
        chat_provider=CountingChatProvider("unused"),
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=StubReranker(error=error),
        candidate_k=20,
        reranker_allow_fallback=False,
    )

    with pytest.raises(AppError) as raised:
        await service.answer(uuid4(), "敏感问题全文", 1)

    assert raised.value is error


@pytest.mark.asyncio
async def test_allowed_reranker_failure_falls_back_without_logging_content(caplog) -> None:
    chunks = [_chunk(), _chunk(), _chunk()]
    error = AppError(
        code="RERANKER_PROVIDER_ERROR",
        message="绝密异常消息。",
        status_code=502,
    )
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever(chunks),
        chat_provider=CountingChatProvider("答案。[1][2]"),
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=StubReranker(error=error),
        candidate_k=3,
        reranker_allow_fallback=True,
    )

    token = set_request_id("rerank-request-001")
    try:
        result = await service.answer(uuid4(), "敏感问题全文", 2)
    finally:
        reset_request_id(token)

    assert [item.document_id for item in result.citations] == [
        chunks[0].document_id,
        chunks[1].document_id,
    ]
    assert result.retrieved_chunk_count == 2
    assert len(caplog.records) == 1
    assert caplog.records[0].error_code == "RERANKER_PROVIDER_ERROR"
    assert caplog.records[0].reranker_provider == "StubReranker"
    assert caplog.records[0].request_id == "rerank-request-001"
    assert "敏感问题全文" not in caplog.text
    assert "绝密异常消息" not in caplog.text
    assert all(chunk.content not in caplog.text for chunk in chunks)


@pytest.mark.asyncio
async def test_allowed_invalid_reranker_scores_fall_back_in_original_order() -> None:
    chunks = [_chunk(), _chunk(), _chunk()]
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever(chunks),
        chat_provider=CountingChatProvider("答案。[1][2]"),
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=StubReranker(scores=[0.1, float("inf"), 0.3]),
        candidate_k=3,
        reranker_allow_fallback=True,
    )

    result = await service.answer(uuid4(), "年假有几天？", 2)

    assert [item.document_id for item in result.citations] == [
        chunks[0].document_id,
        chunks[1].document_id,
    ]
    assert result.retrieved_chunk_count == 2


@pytest.mark.asyncio
async def test_acceptance_gate_error_never_uses_provider_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = AppError(
        code="RERANKER_PROVIDER_ERROR",
        message="接受门失败。",
        status_code=502,
    )

    def fail_acceptance_gate(chunks, *, min_score):
        raise error

    monkeypatch.setattr("app.rag.service.accept_reranked_chunks", fail_acceptance_gate)
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever([_chunk()]),
        chat_provider=CountingChatProvider("不应回退生成"),
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=StubReranker(scores=[0.9]),
        candidate_k=1,
        reranker_allow_fallback=True,
    )

    with pytest.raises(AppError) as raised:
        await service.answer(uuid4(), "问题", 1)

    assert raised.value is error


@pytest.mark.asyncio
async def test_stream_uses_enabled_reranker_order_and_top_k() -> None:
    chunks = [_chunk(), _chunk(), _chunk()]
    retriever = StubRetriever(chunks)
    reranker = StubReranker(scores=[0.1, 0.9, 0.3])
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=retriever,
        chat_provider=StreamingCountingChatProvider("unused", ["答案。[1][2]"]),
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=reranker,
        candidate_k=3,
        reranker_allow_fallback=False,
    )

    events = [item async for item in service.stream_answer(uuid4(), "年假有几天？", 2, [])]
    citations = [item.data for item in events if item.event == "citation"]

    assert retriever.calls[0]["top_k"] == 3
    assert reranker.calls == [("年假有几天？", [chunk.content for chunk in chunks])]
    assert [item["document_id"] for item in citations] == [
        str(chunks[1].document_id),
        str(chunks[2].document_id),
    ]
    assert events[-1].data["retrieved_chunk_count"] == 2


@pytest.mark.asyncio
async def test_stream_successful_low_score_rerank_refuses_without_chat() -> None:
    chunks = [_chunk(), _chunk()]
    chat = StreamingCountingChatProvider("不应该被调用", ["不应该生成"])
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever(chunks),
        chat_provider=chat,
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=StubReranker(scores=[-2.0, -3.0]),
        candidate_k=2,
        reranker_allow_fallback=True,
        reranker_min_score=0.0,
    )

    events = [item async for item in service.stream_answer(uuid4(), "无关问题", 2, [])]

    assert [item.event for item in events] == [
        "rewrite",
        "status",
        "retrieval",
        "token",
        "done",
    ]
    assert events[1].data == {"phase": "retrieving"}
    assert events[2].data["retrieved_chunk_count"] == 0
    assert events[3].data == {"delta": "未找到足够依据，无法根据当前知识库回答该问题。"}
    assert events[4].data["citations"] == []
    assert events[4].data["retrieved_chunk_count"] == 0
    assert events[4].data["timings"]["generation_ms"] == 0
    assert chat.call_count == 0


@pytest.mark.asyncio
async def test_allowed_fallback_does_not_swallow_other_app_errors() -> None:
    error = AppError(code="OTHER_ERROR", message="其他错误。", status_code=502)
    service = RagService(
        session=FakeSession(object()),
        embedding_provider=FakeEmbeddingProvider(dimensions=512),
        retriever=StubRetriever([_chunk()]),
        chat_provider=CountingChatProvider("unused"),
        question_rewriter=RecordingRewriter("不应调用"),
        score_threshold=0.55,
        reranker=StubReranker(error=error),
        candidate_k=20,
        reranker_allow_fallback=True,
    )

    with pytest.raises(AppError) as raised:
        await service.answer(uuid4(), "问题", 1)

    assert raised.value is error

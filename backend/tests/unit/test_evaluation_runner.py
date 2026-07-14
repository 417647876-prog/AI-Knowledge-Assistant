from uuid import UUID, uuid4

import pytest

from app.evaluation.runner import evaluate_cases
from app.evaluation.schemas import EvaluationCase, ExpectedSource
from app.rag.schemas import Citation, QuestionAnswer, RetrievedChunk


class StubEmbeddingProvider:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.1, 0.2]


class StubRetriever:
    def __init__(self, chunks: list[RetrievedChunk]) -> None:
        self.chunks = chunks
        self.calls: list[dict[str, object]] = []

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        query: str,
        query_embedding: list[float],
        top_k: int,
        score_threshold: float,
    ) -> list[RetrievedChunk]:
        self.calls.append(
            {
                "knowledge_base_id": knowledge_base_id,
                "query": query,
                "query_embedding": query_embedding,
                "top_k": top_k,
                "score_threshold": score_threshold,
            }
        )
        return self.chunks


class StubAnswerer:
    def __init__(self, answer: QuestionAnswer) -> None:
        self.answer = answer
        self.case_ids: list[str] = []

    async def answer_case(
        self, *, knowledge_base_id: UUID, case: EvaluationCase, top_k: int
    ) -> QuestionAnswer:
        self.case_ids.append(case.id)
        return self.answer


class SequentialRetriever(StubRetriever):
    def __init__(self, responses: list[list[RetrievedChunk]]) -> None:
        super().__init__([])
        self.responses = responses

    async def search(
        self,
        *,
        knowledge_base_id: UUID,
        query: str,
        query_embedding: list[float],
        top_k: int,
        score_threshold: float,
    ) -> list[RetrievedChunk]:
        response_index = len(self.calls)
        await super().search(
            knowledge_base_id=knowledge_base_id,
            query=query,
            query_embedding=query_embedding,
            top_k=top_k,
            score_threshold=score_threshold,
        )
        return self.responses[response_index]


class SequentialAnswerer:
    def __init__(self, answers: list[QuestionAnswer]) -> None:
        self.answers = answers
        self.case_ids: list[str] = []

    async def answer_case(
        self, *, knowledge_base_id: UUID, case: EvaluationCase, top_k: int
    ) -> QuestionAnswer:
        answer_index = len(self.case_ids)
        self.case_ids.append(case.id)
        return self.answers[answer_index]


def make_chunk(*, file_name: str, content: str) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_name=file_name,
        content=content,
        relevance_score=0.92,
    )


@pytest.mark.asyncio
async def test_evaluate_cases_runs_single_case_with_stable_metadata() -> None:
    knowledge_base_id = uuid4()
    case = EvaluationCase(
        id="keyword-001",
        category="keyword",
        question="试用期多久？",
        expected_sources=[ExpectedSource(file_name="员工手册.docx", contains="三个月")],
    )
    chunk = make_chunk(file_name="员工手册.docx", content="试用期为三个月。")
    citation = Citation(
        citation_id=1,
        document_id=chunk.document_id,
        file_name=chunk.file_name,
        content=chunk.content,
        relevance_score=chunk.relevance_score,
    )
    embedding_provider = StubEmbeddingProvider()
    retriever = StubRetriever([chunk])
    answerer = StubAnswerer(
        QuestionAnswer(answer="试用期为三个月。", citations=[citation], retrieved_chunk_count=1)
    )

    report = await evaluate_cases(
        cases=[case],
        knowledge_base_id=knowledge_base_id,
        embedding_provider=embedding_provider,
        retriever=retriever,
        answerer=answerer,
        top_k=5,
        score_threshold=0.55,
        environment={"embedding_provider": "fake"},
    )

    assert embedding_provider.queries == ["试用期多久？"]
    assert retriever.calls == [
        {
            "knowledge_base_id": knowledge_base_id,
            "query": "试用期多久？",
            "query_embedding": [0.1, 0.2],
            "top_k": 5,
            "score_threshold": 0.55,
        }
    ]
    assert answerer.case_ids == ["keyword-001"]
    assert report.mode == "vector"
    assert len(report.dataset_sha256) == 64
    assert report.environment == {"embedding_provider": "fake"}
    assert report.case_count == 1
    assert report.recall_at_5 == 1.0
    assert report.mrr_at_5 == 1.0
    assert report.citation_hit_rate == 1.0
    assert report.refusal_accuracy == 1.0
    assert report.cases[0].retrieved_files == ["员工手册.docx"]
    assert report.cases[0].citation_files == ["员工手册.docx"]
    assert report.cases[0].latency_ms >= 0


@pytest.mark.asyncio
async def test_evaluate_cases_preserves_case_and_retrieval_order_in_summary() -> None:
    relevant = make_chunk(file_name="年假制度.txt", content="员工享有五天年假。")
    unrelated_same_file = make_chunk(file_name="年假制度.txt", content="年假需要提前申请。")
    cases = [
        EvaluationCase(
            id="semantic-001",
            category="semantic",
            question="我有几天年假？",
            expected_sources=[ExpectedSource(file_name="年假制度.txt", contains="五天年假")],
        ),
        EvaluationCase(
            id="refusal-001",
            category="refusal",
            question="公司食堂今天吃什么？",
            expected_sources=[],
            should_refuse=True,
        ),
    ]
    citation = Citation(
        citation_id=1,
        document_id=relevant.document_id,
        file_name=relevant.file_name,
        content=relevant.content,
        relevance_score=relevant.relevance_score,
    )
    embedding_provider = StubEmbeddingProvider()
    retriever = SequentialRetriever([[unrelated_same_file, relevant], []])
    answerer = SequentialAnswerer(
        [
            QuestionAnswer(answer="五天。", citations=[citation], retrieved_chunk_count=2),
            QuestionAnswer(answer="资料中没有相关信息。", citations=[], retrieved_chunk_count=0),
        ]
    )

    report = await evaluate_cases(
        cases=cases,
        knowledge_base_id=uuid4(),
        embedding_provider=embedding_provider,
        retriever=retriever,
        answerer=answerer,
        top_k=5,
        score_threshold=0.5,
    )

    assert embedding_provider.queries == ["我有几天年假？", "公司食堂今天吃什么？"]
    assert answerer.case_ids == ["semantic-001", "refusal-001"]
    assert [result.case_id for result in report.cases] == ["semantic-001", "refusal-001"]
    assert report.cases[0].retrieved_files == ["年假制度.txt", "年假制度.txt"]
    assert report.recall_at_5 == 1.0
    assert report.mrr_at_5 == pytest.approx(0.75)
    assert report.citation_hit_rate == 1.0
    assert report.refusal_accuracy == 1.0
    assert report.latency_p95_ms >= report.latency_p50_ms >= 0


@pytest.mark.asyncio
async def test_evaluate_cases_rejects_inputs_that_cannot_produce_recall_at_5() -> None:
    with pytest.raises(ValueError, match="评估案例不能为空"):
        await evaluate_cases(
            cases=[],
            knowledge_base_id=uuid4(),
            embedding_provider=StubEmbeddingProvider(),
            retriever=StubRetriever([]),
            answerer=StubAnswerer(QuestionAnswer(answer="", citations=[], retrieved_chunk_count=0)),
            top_k=5,
            score_threshold=0.5,
        )

    case = EvaluationCase(
        id="keyword-002",
        category="keyword",
        question="报销上限？",
        expected_sources=[ExpectedSource(file_name="报销制度.pdf", contains="上限")],
    )
    with pytest.raises(ValueError, match="top_k 不能小于 5"):
        await evaluate_cases(
            cases=[case],
            knowledge_base_id=uuid4(),
            embedding_provider=StubEmbeddingProvider(),
            retriever=StubRetriever([]),
            answerer=StubAnswerer(QuestionAnswer(answer="", citations=[], retrieved_chunk_count=0)),
            top_k=4,
            score_threshold=0.5,
        )

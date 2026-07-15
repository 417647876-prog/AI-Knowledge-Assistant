import asyncio
import json
from uuid import uuid4

import pytest

from app.ai.rerankers import FakeRerankerProvider
from app.core.config import Settings
from app.core.exceptions import AppError
from app.evaluation.schemas import CaseResult, EvaluationCase, EvaluationReport
from app.rag.schemas import QuestionAnswer, RetrievedChunk
from app.rag.service import RagService
from scripts.evaluate_rag import (
    RagServiceEvaluationAnswerer,
    build_evaluation_settings,
    build_safe_environment,
    format_safe_error,
    parse_args,
    run_evaluation,
    run_evaluation_command,
    write_report,
)


class StubEmbeddingProvider:
    def __init__(self) -> None:
        self.queries: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return [0.1, 0.2]


class StubRetriever:
    def __init__(self, chunks: RetrievedChunk | list[RetrievedChunk]) -> None:
        self._chunks = chunks if isinstance(chunks, list) else [chunks]
        self.calls: list[dict[str, object]] = []

    async def search(self, **kwargs) -> list[RetrievedChunk]:
        self.calls.append(kwargs)
        return self._chunks


class StubSession:
    async def get(self, model, key):
        return object()


class StubChatProvider:
    async def generate(self, system_prompt: str, user_prompt: str) -> str:
        return "重排后的答案。[1]"


class SlowReranker:
    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        await asyncio.sleep(0.05)
        return [1.0 for _ in documents]


class FailingReranker:
    async def rerank(self, query: str, documents: list[str]) -> list[float]:
        raise AppError(
            code="RERANKER_PROVIDER_ERROR",
            message="重排序失败。",
            status_code=502,
        )


class StubAnswerer:
    async def answer_case(self, **kwargs) -> QuestionAnswer:
        return QuestionAnswer(answer="测试答案", citations=[], retrieved_chunk_count=1)


class StubRagService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str, int]] = []

    async def answer(self, knowledge_base_id: object, question: str, top_k: int) -> QuestionAnswer:
        self.calls.append((knowledge_base_id, question, top_k))
        return QuestionAnswer(answer="答案", citations=[], retrieved_chunk_count=1)


def test_parse_args_accepts_vector_baseline_inputs() -> None:
    knowledge_base_id = uuid4()

    args = parse_args(
        [
            "--dataset",
            "tests/fixtures/evaluation/stage3.jsonl",
            "--knowledge-base-id",
            str(knowledge_base_id),
            "--mode",
            "vector",
            "--output",
            "reports/stage3a-vector-baseline.json",
        ]
    )

    assert args.dataset.name == "stage3.jsonl"
    assert args.knowledge_base_id == knowledge_base_id
    assert args.mode == "vector"
    assert args.output.name == "stage3a-vector-baseline.json"

    hybrid_args = parse_args(
        [
            "--dataset",
            "tests/fixtures/evaluation/stage3.jsonl",
            "--knowledge-base-id",
            str(knowledge_base_id),
            "--mode",
            "hybrid",
            "--output",
            "reports/stage3b-hybrid.json",
        ]
    )
    assert hybrid_args.mode == "hybrid"

    rerank_args = parse_args(
        [
            "--dataset",
            "tests/fixtures/evaluation/stage3.jsonl",
            "--knowledge-base-id",
            str(knowledge_base_id),
            "--mode",
            "rerank",
            "--output",
            "reports/stage3c-rerank.json",
        ]
    )
    assert rerank_args.mode == "rerank"


def test_parse_args_rejects_unknown_mode_and_top_k_below_five() -> None:
    knowledge_base_id = uuid4()
    required_args = [
        "--dataset",
        "tests/fixtures/evaluation/stage3.jsonl",
        "--knowledge-base-id",
        str(knowledge_base_id),
        "--output",
        "reports/baseline.json",
    ]

    with pytest.raises(SystemExit):
        parse_args([*required_args, "--mode", "rewrite"])
    with pytest.raises(SystemExit):
        parse_args([*required_args, "--mode", "vector", "--top-k", "4"])


def test_write_report_uses_schema_and_excludes_configuration_secrets(tmp_path) -> None:
    database_url = "postgresql+psycopg://private-user:private-password@db.example/private"
    embedding_key = "embedding-secret-key"
    chat_key = "chat-secret-key"
    settings = Settings(
        database_url=database_url,
        embedding_provider="fake",
        embedding_api_key=embedding_key,
        chat_provider="fake",
        chat_api_key=chat_key,
    )
    report = EvaluationReport(
        mode="vector",
        dataset_sha256="a" * 64,
        top_k=5,
        case_count=1,
        recall_at_5=1.0,
        mrr_at_5=1.0,
        citation_hit_rate=1.0,
        refusal_accuracy=1.0,
        latency_p50_ms=2.0,
        latency_p95_ms=2.0,
        environment=build_safe_environment(settings),
        cases=[
            CaseResult(
                case_id="keyword-001",
                retrieved_files=["员工手册.docx"],
                citation_files=["员工手册.docx"],
                recall_at_k=1.0,
                reciprocal_rank=1.0,
                refused=False,
                refusal_correct=True,
                latency_ms=2.0,
            )
        ],
    )
    output = tmp_path / "reports" / "baseline.json"

    write_report(report, output)

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert {
        "schema_version",
        "mode",
        "dataset_sha256",
        "top_k",
        "case_count",
        "recall_at_5",
        "mrr_at_5",
        "citation_hit_rate",
        "refusal_accuracy",
        "latency_p50_ms",
        "latency_p95_ms",
        "environment",
        "cases",
    } <= payload.keys()
    serialized = output.read_text(encoding="utf-8")
    assert database_url not in serialized
    assert embedding_key not in serialized
    assert chat_key not in serialized


def test_safe_environment_records_reranker_candidate_count_and_model() -> None:
    settings = Settings(
        rag_reranker_provider="local",
        rag_reranker_model="BAAI/bge-reranker-base",
        rag_candidate_k=20,
    )

    environment = build_safe_environment(settings)

    assert environment["rag_candidate_k"] == "20"
    assert environment["rag_reranker_model"] == "BAAI/bge-reranker-base"


def test_rerank_mode_uses_hybrid_candidates_and_strict_local_reranker() -> None:
    settings = build_evaluation_settings(Settings(), "rerank")

    assert settings.rag_retrieval_mode == "hybrid"
    assert settings.rag_reranker_provider == "local"
    assert settings.rag_reranker_allow_fallback is False


@pytest.mark.asyncio
async def test_run_evaluation_loads_dataset_and_sets_safe_environment(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        '{"id":"keyword-001","category":"keyword","question":"试用期多久？",'
        '"expected_sources":[{"file_name":"员工手册.docx","contains":"三个月"}]}\n',
        encoding="utf-8",
    )
    knowledge_base_id = uuid4()
    chunk = RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_name="员工手册.docx",
        content="试用期为三个月。",
        relevance_score=0.9,
    )

    report = await run_evaluation(
        dataset=dataset,
        knowledge_base_id=knowledge_base_id,
        settings=Settings(embedding_provider="fake", chat_provider="fake"),
        embedding_provider=StubEmbeddingProvider(),
        retriever=StubRetriever(chunk),
        answerer=StubAnswerer(),
        top_k=5,
        mode="hybrid",
    )

    assert report.mode == "hybrid"
    assert report.case_count == 1
    assert report.environment["embedding_provider"] == "fake"
    assert report.cases[0].retrieved_files == ["员工手册.docx"]


@pytest.mark.asyncio
async def test_rerank_evaluation_metrics_use_final_reranked_order(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        '{"id":"semantic-001","category":"semantic","question":"年假有几天？",'
        '"expected_sources":[{"file_name":"正确.md","contains":"五天年假"}]}\n',
        encoding="utf-8",
    )
    wrong = RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_name="错误.md",
        content="年假需要提前申请。",
        relevance_score=0.9,
    )
    correct = RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_name="正确.md",
        content="员工享有五天年假。",
        relevance_score=0.8,
    )
    embedding_provider = StubEmbeddingProvider()
    retriever = StubRetriever([wrong, correct])
    service = RagService(
        session=StubSession(),
        embedding_provider=embedding_provider,
        retriever=retriever,
        chat_provider=StubChatProvider(),
        question_rewriter=object(),
        score_threshold=0.55,
        reranker=FakeRerankerProvider(scores=[0.1, 0.9]),
        candidate_k=20,
        reranker_allow_fallback=False,
    )

    report = await run_evaluation(
        dataset=dataset,
        knowledge_base_id=uuid4(),
        settings=Settings(embedding_provider="fake", chat_provider="fake"),
        embedding_provider=embedding_provider,
        retriever=retriever,
        answerer=RagServiceEvaluationAnswerer(service),
        top_k=5,
        mode="rerank",
    )

    assert report.cases[0].retrieved_files == ["正确.md", "错误.md"]
    assert report.cases[0].citation_files == ["正确.md"]
    assert report.mrr_at_5 == 1.0
    assert len(retriever.calls) == 1
    assert retriever.calls[0]["top_k"] == 20
    assert embedding_provider.queries == ["年假有几天？"]


@pytest.mark.asyncio
async def test_rerank_evaluation_latency_includes_reranker(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        '{"id":"keyword-001","category":"keyword","question":"密码几位？",'
        '"expected_sources":[{"file_name":"安全.md","contains":"十二位"}]}\n',
        encoding="utf-8",
    )
    chunk = RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_name="安全.md",
        content="密码至少十二位。",
        relevance_score=0.9,
    )
    embedding_provider = StubEmbeddingProvider()
    retriever = StubRetriever(chunk)
    service = RagService(
        session=StubSession(),
        embedding_provider=embedding_provider,
        retriever=retriever,
        chat_provider=StubChatProvider(),
        question_rewriter=object(),
        score_threshold=0.55,
        reranker=SlowReranker(),
        candidate_k=20,
        reranker_allow_fallback=False,
    )

    report = await run_evaluation(
        dataset=dataset,
        knowledge_base_id=uuid4(),
        settings=Settings(embedding_provider="fake", chat_provider="fake"),
        embedding_provider=embedding_provider,
        retriever=retriever,
        answerer=RagServiceEvaluationAnswerer(service),
        top_k=5,
        mode="rerank",
    )

    assert report.latency_p50_ms >= 40
    assert report.latency_p95_ms >= 40


@pytest.mark.asyncio
async def test_rerank_evaluation_strict_provider_error_stops_single_retrieval(tmp_path) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        '{"id":"keyword-001","category":"keyword","question":"密码几位？",'
        '"expected_sources":[{"file_name":"安全.md","contains":"十二位"}]}\n',
        encoding="utf-8",
    )
    chunk = RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_name="安全.md",
        content="密码至少十二位。",
        relevance_score=0.9,
    )
    embedding_provider = StubEmbeddingProvider()
    retriever = StubRetriever(chunk)
    service = RagService(
        session=StubSession(),
        embedding_provider=embedding_provider,
        retriever=retriever,
        chat_provider=StubChatProvider(),
        question_rewriter=object(),
        score_threshold=0.55,
        reranker=FailingReranker(),
        candidate_k=20,
        reranker_allow_fallback=False,
    )

    with pytest.raises(AppError, match="重排序失败"):
        await run_evaluation(
            dataset=dataset,
            knowledge_base_id=uuid4(),
            settings=Settings(embedding_provider="fake", chat_provider="fake"),
            embedding_provider=embedding_provider,
            retriever=retriever,
            answerer=RagServiceEvaluationAnswerer(service),
            top_k=5,
            mode="rerank",
        )

    assert len(retriever.calls) == 1
    assert embedding_provider.queries == ["密码几位？"]


@pytest.mark.asyncio
async def test_rag_service_answerer_uses_case_question() -> None:
    service = StubRagService()
    answerer = RagServiceEvaluationAnswerer(service)
    knowledge_base_id = uuid4()
    case = EvaluationCase(
        id="keyword-002",
        category="keyword",
        question="年假有几天？",
        expected_sources=[{"file_name": "年假制度.txt", "contains": "五天"}],
    )

    answer = await answerer.answer_case(
        knowledge_base_id=knowledge_base_id,
        case=case,
        top_k=5,
    )

    assert answer.answer == "答案"
    assert service.calls == [(knowledge_base_id, "年假有几天？", 5)]


@pytest.mark.asyncio
async def test_evaluation_rag_service_old_constructor_keeps_reranker_disabled() -> None:
    chunk = RetrievedChunk(
        chunk_id=uuid4(),
        document_id=uuid4(),
        file_name="年假制度.txt",
        content="年假五天。",
        relevance_score=0.9,
    )
    retriever = StubRetriever(chunk)
    service = RagService(
        session=object(),
        embedding_provider=StubEmbeddingProvider(),
        retriever=retriever,
        chat_provider=object(),
        question_rewriter=object(),
        score_threshold=0.55,
    )

    result = await service._retrieve(uuid4(), "年假", 7)

    assert result == [chunk]
    assert service._reranker is None
    assert retriever.calls[0]["top_k"] == 7


def test_format_safe_error_does_not_echo_connection_or_api_secret() -> None:
    secret = "postgresql://user:password@private.example/db?api_key=secret"

    message = format_safe_error(RuntimeError(secret))

    assert secret not in message
    assert "RuntimeError" in message


def test_run_evaluation_command_uses_selector_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    observed_loop: asyncio.AbstractEventLoop | None = None
    settings = Settings(embedding_provider="fake", chat_provider="fake")

    async def fake_run_from_args(*args, **kwargs) -> EvaluationReport:
        nonlocal observed_loop
        observed_loop = asyncio.get_running_loop()
        return EvaluationReport(
            mode="vector",
            dataset_sha256="a" * 64,
            top_k=5,
            case_count=1,
            recall_at_5=1.0,
            mrr_at_5=1.0,
            citation_hit_rate=1.0,
            refusal_accuracy=1.0,
            latency_p50_ms=1.0,
            latency_p95_ms=1.0,
            environment={},
            cases=[],
        )

    monkeypatch.setattr("scripts.evaluate_rag.run_from_args", fake_run_from_args)

    report = run_evaluation_command(
        parse_args(
            [
                "--dataset",
                "tests/fixtures/evaluation/stage3.jsonl",
                "--knowledge-base-id",
                str(uuid4()),
                "--mode",
                "vector",
                "--output",
                "reports/baseline.json",
            ]
        ),
        settings,
    )

    assert report.mode == "vector"
    assert isinstance(observed_loop, asyncio.SelectorEventLoop)

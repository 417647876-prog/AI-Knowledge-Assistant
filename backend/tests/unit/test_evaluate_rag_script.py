import asyncio
import json
from uuid import uuid4

import pytest

from app.core.config import Settings
from app.evaluation.schemas import CaseResult, EvaluationCase, EvaluationReport
from app.rag.schemas import QuestionAnswer, RetrievedChunk
from scripts.evaluate_rag import (
    RagServiceEvaluationAnswerer,
    build_safe_environment,
    format_safe_error,
    parse_args,
    run_evaluation,
    run_evaluation_command,
    write_report,
)


class StubEmbeddingProvider:
    async def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2]


class StubRetriever:
    def __init__(self, chunk: RetrievedChunk) -> None:
        self._chunk = chunk

    async def search(self, **kwargs) -> list[RetrievedChunk]:
        return [self._chunk]


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
        parse_args([*required_args, "--mode", "rerank"])
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

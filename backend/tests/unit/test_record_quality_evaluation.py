import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.db.models import QualityEvaluationRun
from app.evaluation.policy import Stage3QualityPolicy
from scripts.record_quality_evaluation import (
    import_quality_evaluation,
    main,
    parse_quality_evaluation,
    record_quality_evaluation,
)


def _policy() -> Stage3QualityPolicy:
    return Stage3QualityPolicy.model_validate(
        {
            "schema_version": "1.0",
            "final_mode": "rewrite",
            "minimum_case_count": 30,
            "required_categories": [
                "keyword",
                "semantic",
                "refusal",
                "multi_turn",
                "interference",
            ],
            "historical_thresholds": {
                "stage3b_keyword_gain": 0.10,
                "stage3c_mrr_relative_gain": 0.05,
                "stage3d_multi_turn_gain": 0.15,
            },
            "final_thresholds": {
                "recall_at_5": 0.85,
                "citation_hit_rate": 0.90,
                "refusal_accuracy": 0.90,
            },
            "waivers": [
                {
                    "gate_id": "stage3c.mrr_relative_gain",
                    "approved_on": "2026-07-15",
                    "minimum_allowed": 0.0,
                    "reason": "既有阶段3豁免，仅用于完整策略 schema",
                    "evidence": "docs/evidence.md",
                }
            ],
        }
    )


def _report_data() -> dict[str, object]:
    categories = ("keyword", "semantic", "refusal", "multi_turn", "interference")
    cases = [
        {
            "case_id": f"case-{index:02d}",
            "category": categories[index % len(categories)],
            "retrieved_files": [f"敏感文件-{index}.pdf"],
            "citation_files": [f"敏感引用-{index}.pdf"],
            "accepted_chunk_count": 2,
            "recall_at_k": 1.0,
            "reciprocal_rank": 1.0,
            "citation_hit_rate": 1.0,
            "refused": False,
            "refusal_correct": True,
            "latency_ms": 10.4,
        }
        for index in range(30)
    ]
    return {
        "schema_version": "1.1",
        "mode": "rewrite",
        "dataset_sha256": "a" * 64,
        "top_k": 5,
        "case_count": 30,
        "recall_at_5": 0.91,
        "mrr_at_5": 0.92,
        "citation_hit_rate": 0.93,
        "refusal_accuracy": 0.94,
        "latency_p50_ms": 10.0,
        "latency_p95_ms": 20.0,
        "environment": {
            "app_env": "test",
            "chat_model": "safe-model-id",
            "rag_retrieval_mode": "hybrid",
            "prompt": "敏感提示词",
            "knowledge_base_name": "敏感知识库名",
            "file_name": "敏感文件名.pdf",
        },
        "provenance": {
            "run_id": "d7e1cc82-b2ee-4df0-a8b1-b0f73b8bd71c",
            "knowledge_base_id": "7488952f-a3bd-46f5-8b41-2bb6ac99c3a5",
            "snapshot_sha256": "b" * 64,
            "document_count": 2,
            "chunk_count": 3,
            "generated_at": "2026-07-16T08:00:00Z",
        },
        "cases": cases,
    }


def test_parse_quality_evaluation_keeps_only_sanitized_aggregate_summary() -> None:
    report_bytes = json.dumps(_report_data(), ensure_ascii=False, separators=(",", ":")).encode()

    summary = parse_quality_evaluation(report_bytes, _policy())

    assert summary.dataset_hash == "a" * 64
    assert summary.report_hash == hashlib.sha256(report_bytes).hexdigest()
    assert summary.mode == "rewrite"
    assert summary.metrics == {
        "case_count": 30,
        "top_k": 5,
        "recall_at_5": 0.91,
        "mrr_at_5": 0.92,
        "citation_hit_rate": 0.93,
        "refusal_accuracy": 0.94,
        "latency_p50_ms": 10.0,
        "latency_p95_ms": 20.0,
    }
    assert summary.model_config_summary == {
        "app_env": "test",
        "chat_model": "safe-model-id",
        "rag_retrieval_mode": "hybrid",
    }
    assert summary.gate_passed is True
    assert summary.completed_at == datetime(2026, 7, 16, 8, tzinfo=UTC)
    assert summary.duration_ms == 312
    assert summary.started_at == summary.completed_at - timedelta(milliseconds=312)
    serialized = repr(summary)
    for sensitive in (
        "敏感文件-0.pdf",
        "敏感引用-0.pdf",
        "敏感提示词",
        "敏感知识库名",
        "敏感文件名.pdf",
    ):
        assert sensitive not in serialized


def test_parse_quality_evaluation_rejects_unknown_fields_recursively() -> None:
    reports = []
    top_level = deepcopy(_report_data())
    top_level["question"] = "不允许的案例问题"
    reports.append(top_level)
    provenance = deepcopy(_report_data())
    provenance["provenance"]["knowledge_base_name"] = "不允许的知识库名"  # type: ignore[index]
    reports.append(provenance)
    case = deepcopy(_report_data())
    case["cases"][0]["answer"] = "不允许的案例答案"  # type: ignore[index]
    case["cases"][0]["citation_content"] = "不允许的引用正文"  # type: ignore[index]
    reports.append(case)

    for report in reports:
        with pytest.raises(ValueError, match="未知字段"):
            parse_quality_evaluation(
                json.dumps(report, ensure_ascii=False).encode(),
                _policy(),
            )


@pytest.mark.asyncio
async def test_record_quality_evaluation_returns_inserted_or_same_existing_run() -> None:
    summary = parse_quality_evaluation(
        json.dumps(_report_data(), ensure_ascii=False).encode(),
        _policy(),
    )
    existing = QualityEvaluationRun(
        dataset_hash=summary.dataset_hash,
        mode=summary.mode,
        model_config_summary=summary.model_config_summary,
        metrics=summary.metrics,
        report_hash=summary.report_hash,
        gate_passed=summary.gate_passed,
        started_at=summary.started_at,
        completed_at=summary.completed_at,
        duration_ms=summary.duration_ms,
    )

    class FakeSession:
        def __init__(self, results: list[QualityEvaluationRun | None]) -> None:
            self.results = results
            self.statements = []

        async def scalar(self, statement):
            self.statements.append(statement)
            return self.results.pop(0)

    inserted_session = FakeSession([existing])
    conflict_session = FakeSession([None, existing])

    inserted = await record_quality_evaluation(inserted_session, summary)  # type: ignore[arg-type]
    duplicate = await record_quality_evaluation(conflict_session, summary)  # type: ignore[arg-type]

    assert inserted is existing
    assert duplicate is existing
    assert "ON CONFLICT" in str(inserted_session.statements[0])
    assert "DO NOTHING" in str(inserted_session.statements[0])
    assert len(conflict_session.statements) == 2


@pytest.mark.asyncio
async def test_import_quality_evaluation_commits_at_command_transaction_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").mkdir()
    evidence = tmp_path / "docs" / "evidence.md"
    evidence.parent.mkdir()
    evidence.write_text("# 脱敏证据\n", encoding="utf-8")
    report_path = tmp_path / "report.json"
    report_path.write_text(
        json.dumps(_report_data(), ensure_ascii=False),
        encoding="utf-8",
    )
    policy_data = _policy().model_dump(mode="json")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy_data, ensure_ascii=False), encoding="utf-8")
    existing = QualityEvaluationRun(
        id=uuid4(),
        dataset_hash="a" * 64,
        mode="rewrite",
        model_config_summary={},
        metrics={},
        report_hash="c" * 64,
        gate_passed=True,
        started_at=datetime(2026, 7, 16, 8, tzinfo=UTC),
        completed_at=datetime(2026, 7, 16, 8, tzinfo=UTC),
        duration_ms=0,
    )

    class FakeSession:
        async def scalar(self, statement):
            return existing

    class Transaction:
        def __init__(self) -> None:
            self.exit_error = object()

        async def __aenter__(self):
            return FakeSession()

        async def __aexit__(self, error_type, error, traceback):
            self.exit_error = error

    transaction = Transaction()

    class Factory:
        def begin(self):
            return transaction

    monkeypatch.setattr("scripts.record_quality_evaluation.session_factory", Factory())

    result = await import_quality_evaluation(report_path, policy_path)

    assert result is existing
    assert transaction.exit_error is None


def test_main_accepts_report_and_policy_and_prints_only_safe_identifiers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report_path = tmp_path / "敏感报告名.json"
    policy_path = tmp_path / "敏感策略名.json"
    calls = []

    def fake_run_import(report: Path, policy: Path):
        calls.append((report, policy))
        return SimpleNamespace(id=uuid4(), report_hash="d" * 64)

    monkeypatch.setattr("scripts.record_quality_evaluation.run_import", fake_run_import)

    main(["--report", str(report_path), "--policy", str(policy_path)])

    assert calls == [(report_path, policy_path)]
    output = capsys.readouterr().out
    assert "id=" in output
    assert "report_hash=" + "d" * 64 in output
    assert str(report_path) not in output
    assert str(policy_path) not in output


def test_parse_quality_evaluation_rejects_invalid_contract_and_records_failed_gate() -> None:
    invalid_reports = []
    old_schema = deepcopy(_report_data())
    old_schema["schema_version"] = "1.0"
    invalid_reports.append(old_schema)
    wrong_mode = deepcopy(_report_data())
    wrong_mode["mode"] = "vector"
    invalid_reports.append(wrong_mode)
    bad_dataset_hash = deepcopy(_report_data())
    bad_dataset_hash["dataset_sha256"] = "not-a-sha256"
    invalid_reports.append(bad_dataset_hash)
    too_few_cases = deepcopy(_report_data())
    too_few_cases["cases"] = too_few_cases["cases"][:29]  # type: ignore[index]
    too_few_cases["case_count"] = 29
    invalid_reports.append(too_few_cases)
    missing_categories = deepcopy(_report_data())
    for case in missing_categories["cases"]:  # type: ignore[union-attr]
        case["category"] = "keyword"
    invalid_reports.append(missing_categories)

    for report in invalid_reports:
        with pytest.raises(ValueError):
            parse_quality_evaluation(json.dumps(report).encode(), _policy())

    failed_gate = deepcopy(_report_data())
    failed_gate["recall_at_5"] = 0.84
    summary = parse_quality_evaluation(json.dumps(failed_gate).encode(), _policy())
    assert summary.gate_passed is False


@pytest.mark.asyncio
async def test_import_quality_evaluation_rolls_back_when_database_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".git").mkdir()
    evidence = tmp_path / "docs" / "evidence.md"
    evidence.parent.mkdir()
    evidence.write_text("# 脱敏证据\n", encoding="utf-8")
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(_report_data()), encoding="utf-8")
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(
        json.dumps(_policy().model_dump(mode="json")),
        encoding="utf-8",
    )

    class FailingSession:
        async def scalar(self, statement):
            raise RuntimeError("write failed")

    class Transaction:
        def __init__(self) -> None:
            self.exit_error = None

        async def __aenter__(self):
            return FailingSession()

        async def __aexit__(self, error_type, error, traceback):
            self.exit_error = error

    transaction = Transaction()

    class Factory:
        def begin(self):
            return transaction

    monkeypatch.setattr("scripts.record_quality_evaluation.session_factory", Factory())

    with pytest.raises(RuntimeError, match="write failed"):
        await import_quality_evaluation(report_path, policy_path)

    assert isinstance(transaction.exit_error, RuntimeError)

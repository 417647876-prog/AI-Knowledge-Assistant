from datetime import UTC, date, datetime
from importlib import import_module
from pathlib import Path
from uuid import UUID

import pytest

from app.evaluation.comparison import GateResult, MetricDelta, Stage3Comparison
from app.evaluation.policy import QualityWaiver
from app.evaluation.schemas import CaseResult, EvaluationProvenance, EvaluationReport

MODES = ("vector", "hybrid", "rerank", "rewrite")
CATEGORIES = ("keyword", "semantic", "refusal", "multi_turn", "interference")
GATE_IDS = (
    "stage3a.case_count",
    "stage3a.category_coverage",
    "stage3b.overall_recall",
    "stage3b.keyword_recall",
    "stage3b.citation",
    "stage3b.refusal",
    "stage3c.mrr_relative_gain",
    "stage3c.citation",
    "stage3d.multi_turn_recall",
    "stage3e.recall",
    "stage3e.citation",
    "stage3e.refusal",
)
HEADINGS = [
    "## 1. 验收结论",
    "## 2. 数据集与知识库快照",
    "## 3. 执行环境",
    "## 4. 四模式总体指标",
    "## 5. 各分类 Recall@5",
    "## 6. 相对 vector 的提升或退化",
    "## 7. 3A～3E 质量门结果",
    "## 8. 3C 风险豁免及适用边界",
    "## 9. 失败案例 ID",
    "## 10. 最终推荐配置与纯向量回退配置",
    "## 11. 可重复执行命令",
    "## 12. 已知风险与延迟说明",
]


def _make_cases() -> list[CaseResult]:
    return [
        CaseResult(
            case_id=f"{category}-{index:02d}",
            category=category,
            retrieved_files=[f"{category}.md"],
            citation_files=[f"{category}.md"],
            accepted_chunk_count=1,
            recall_at_k=1.0,
            reciprocal_rank=1.0,
            citation_hit_rate=1.0,
            refused=category == "refusal",
            refusal_correct=True,
            latency_ms=20.0,
        )
        for category in CATEGORIES
        for index in range(1, 7)
    ]


def _make_report(mode: str) -> EvaluationReport:
    metric = 0.9666666667 if mode == "rewrite" else 0.9
    environment = {
        "app_env": "test",
        "embedding_provider": "local",
        "embedding_model": "BAAI/bge-small-zh-v1.5",
        "embedding_device": "cpu",
        "chat_provider": "fake",
        "chat_model": "fake-chat",
        "rag_retrieval_mode": "hybrid" if mode != "vector" else "vector",
        "rag_reranker_provider": "local" if mode in {"rerank", "rewrite"} else "disabled",
        "internal_note": "不应出现在公开报告中",
    }
    return EvaluationReport(
        schema_version="1.1",
        mode=mode,
        dataset_sha256="a" * 64,
        top_k=5,
        case_count=30,
        recall_at_5=metric,
        mrr_at_5=metric,
        citation_hit_rate=metric,
        refusal_accuracy=metric,
        latency_p50_ms=17.72275 if mode == "rewrite" else 10.0,
        latency_p95_ms=40.567905 if mode == "rewrite" else 20.0,
        environment=environment,
        provenance=EvaluationProvenance(
            run_id=UUID("11111111-1111-4111-8111-111111111111"),
            knowledge_base_id=UUID("22222222-2222-4222-8222-222222222222"),
            snapshot_sha256="b" * 64,
            document_count=5,
            chunk_count=13,
            generated_at=datetime(2026, 7, 15, tzinfo=UTC),
        ),
        cases=_make_cases(),
    )


def make_render_comparison(
    *,
    passed: bool,
    stage3c_status: str,
    failure_case_ids: dict[str, list[str]],
) -> Stage3Comparison:
    reports = {mode: _make_report(mode) for mode in MODES}
    deltas = {
        mode: MetricDelta(
            recall_at_5=0.0666666667 if mode == "rewrite" else 0.0,
            mrr_at_5=0.0666666667 if mode == "rewrite" else 0.0,
            citation_hit_rate=0.0666666667 if mode == "rewrite" else 0.0,
            refusal_accuracy=0.0666666667 if mode == "rewrite" else 0.0,
            latency_p50_ms=7.72275 if mode == "rewrite" else 0.0,
            latency_p95_ms=20.567905 if mode == "rewrite" else 0.0,
        )
        for mode in MODES
    }
    category_recall = {
        mode: {category: reports[mode].recall_at_5 for category in CATEGORIES} for mode in MODES
    }
    waiver = QualityWaiver(
        gate_id="stage3c.mrr_relative_gain",
        approved_on=date(2026, 7, 15),
        minimum_allowed=0.0,
        reason="重排没有退化，保留风险豁免并持续观察",
        evidence=Path("docs/阶段3执行进度.md"),
    )
    gates = []
    for gate_id in GATE_IDS:
        status = stage3c_status if gate_id == "stage3c.mrr_relative_gain" else "passed"
        if not passed and gate_id == "stage3e.recall":
            status = "failed"
        gates.append(
            GateResult(
                gate_id=gate_id,
                status=status,
                actual=0.0 if gate_id == "stage3c.mrr_relative_gain" else 0.9666666667,
                target=0.05 if gate_id == "stage3c.mrr_relative_gain" else 0.9,
                message=(
                    "质量门未通过、已获风险豁免"
                    if status == "waived"
                    else f"{gate_id} {'达到' if status == 'passed' else '未达到'}目标"
                ),
                waiver=waiver if status == "waived" else None,
            )
        )
    return Stage3Comparison(
        reports=reports,
        metric_deltas=deltas,
        category_recall=category_recall,
        gates=gates,
        failure_case_ids=failure_case_ids,
        passed=passed,
        sanitized_failures=[] if passed else ["stage3e.recall：未达到目标"],
    )


@pytest.fixture
def passing_comparison() -> Stage3Comparison:
    return make_render_comparison(passed=True, stage3c_status="passed", failure_case_ids={})


@pytest.fixture
def waived_comparison() -> Stage3Comparison:
    return make_render_comparison(passed=True, stage3c_status="waived", failure_case_ids={})


@pytest.fixture
def failing_comparison() -> Stage3Comparison:
    return make_render_comparison(
        passed=False,
        stage3c_status="passed",
        failure_case_ids={"rewrite.recall": ["multi-turn-06"]},
    )


def _render(comparison: Stage3Comparison, reproduce_command: str) -> str:
    module = import_module("app.evaluation.reporting")
    return module.render_stage3_markdown(
        comparison,
        reproduce_command=reproduce_command,
    )


def test_report_has_fixed_heading_and_mode_order(
    passing_comparison: Stage3Comparison,
) -> None:
    markdown = _render(
        passing_comparison,
        "uv run python -m scripts.accept_stage3 --dataset stage3.jsonl",
    )

    assert markdown.startswith("# 阶段 3 RAG 质量综合验收报告\n")
    assert markdown.endswith("\n")
    positions = [markdown.index(heading) for heading in HEADINGS]
    assert positions == sorted(positions)
    mode_positions = [markdown.index(f"| {mode} |") for mode in MODES]
    assert mode_positions == sorted(mode_positions)


def test_report_formats_metrics_and_waiver(
    waived_comparison: Stage3Comparison,
) -> None:
    markdown = _render(waived_comparison, "accept-stage3")

    assert "96.67%" in markdown
    assert "质量门未通过、已获风险豁免" in markdown
    assert "至少 5.00%" in markdown
    assert "实际 0.00%" in markdown
    assert "MRR 负增长或引用下降时豁免不适用" in markdown


def test_report_only_exposes_failure_case_ids(
    failing_comparison: Stage3Comparison,
) -> None:
    markdown = _render(failing_comparison, "accept-stage3")
    provenance = failing_comparison.reports["rewrite"].provenance
    assert provenance is not None

    assert "multi-turn-06" in markdown
    assert "这个问题的全文不能公开" not in markdown
    assert "片段原文不能公开" not in markdown
    assert str(provenance.knowledge_base_id) not in markdown
    assert str(provenance.run_id) in markdown
    assert provenance.snapshot_sha256[:12] in markdown
    assert provenance.snapshot_sha256 not in markdown
    assert "internal_note" not in markdown
    assert "不应出现在公开报告中" not in markdown


@pytest.mark.parametrize(
    "secret",
    [
        "database_url=private",
        "api_key=private",
        "access_token=private",
        "postgresql+psycopg://user:pass@localhost/db",
    ],
)
def test_sensitive_marker_scan_rejects_secret(secret: str) -> None:
    module = import_module("app.evaluation.reporting")

    with pytest.raises(ValueError, match="公开报告包含敏感标记"):
        module.ensure_public_report_safe(secret)

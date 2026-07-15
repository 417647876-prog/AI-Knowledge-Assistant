from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from uuid import uuid4

import pytest

from app.evaluation.policy import Stage3QualityPolicy, load_stage3_quality_policy
from app.evaluation.schemas import CaseResult, EvaluationProvenance, EvaluationReport

MODES = ("vector", "hybrid", "rerank", "rewrite")
CATEGORIES = ("keyword", "semantic", "refusal", "multi_turn", "interference")


@pytest.fixture
def policy() -> Stage3QualityPolicy:
    repo_root = Path(__file__).resolve().parents[3]
    return load_stage3_quality_policy(
        repo_root / "backend/config/evaluation/stage3-quality-policy.json",
        repo_root=repo_root,
    )


def make_cases() -> list[CaseResult]:
    return [
        CaseResult(
            case_id=f"{category}-{index:02d}",
            category=category,
            retrieved_files=[f"{category}.txt"],
            citation_files=[f"{category}.txt"],
            accepted_chunk_count=1,
            recall_at_k=1.0,
            reciprocal_rank=1.0,
            citation_hit_rate=1.0,
            refused=category == "refusal",
            refusal_correct=True,
            latency_ms=10.0,
        )
        for category in CATEGORIES
        for index in range(1, 7)
    ]


def make_environment(mode: str) -> dict[str, str]:
    environment = {
        "app_env": "test",
        "embedding_provider": "local",
        "embedding_model": "BAAI/bge-small-zh-v1.5",
        "embedding_device": "cpu",
        "embedding_batch_size": "32",
        "chat_provider": "fake",
        "chat_model": "fake-chat",
        "embedding_dimensions": "512",
        "rag_score_threshold": "0.55",
        "rag_rrf_rank_constant": "60",
        "rag_reranker_provider": "disabled",
        "rag_reranker_model": "BAAI/bge-reranker-base",
        "rag_reranker_device": "cpu",
        "rag_reranker_batch_size": "16",
        "rag_candidate_k": "20",
        "rag_reranker_allow_fallback": "True",
        "rag_reranker_min_score": "disabled",
    }
    if mode == "vector":
        environment["rag_retrieval_mode"] = "vector"
    elif mode == "hybrid":
        environment["rag_retrieval_mode"] = "hybrid"
    else:
        environment["rag_retrieval_mode"] = "hybrid"
        environment["rag_reranker_provider"] = "local"
        environment["rag_reranker_allow_fallback"] = "False"
    return environment


def make_four_reports() -> list[EvaluationReport]:
    run_id = uuid4()
    knowledge_base_id = uuid4()
    generated_at = datetime.now(UTC)
    values = {
        "vector": (0.83, 0.83, 0.83, 0.83, 20.0, 40.0),
        "hybrid": (0.93, 0.93, 0.93, 0.93, 30.0, 60.0),
        "rerank": (0.93, 0.93, 0.93, 0.93, 70.0, 110.0),
        "rewrite": (0.96, 0.96, 0.96, 0.96, 90.0, 250.0),
    }
    reports: list[EvaluationReport] = []
    for mode in MODES:
        recall, mrr, citation, refusal, p50, p95 = values[mode]
        reports.append(
            EvaluationReport(
                schema_version="1.1",
                mode=mode,
                dataset_sha256="a" * 64,
                top_k=5,
                case_count=30,
                recall_at_5=recall,
                mrr_at_5=mrr,
                citation_hit_rate=citation,
                refusal_accuracy=refusal,
                latency_p50_ms=p50,
                latency_p95_ms=p95,
                environment=make_environment(mode),
                provenance=EvaluationProvenance(
                    run_id=run_id,
                    knowledge_base_id=knowledge_base_id,
                    snapshot_sha256="b" * 64,
                    document_count=5,
                    chunk_count=13,
                    generated_at=generated_at,
                ),
                cases=make_cases(),
            )
        )
    return reports


def set_metric(
    reports: list[EvaluationReport],
    mode: str,
    field: str,
    value: float,
) -> None:
    report = next(item for item in reports if item.mode == mode)
    setattr(report, field, value)


def set_category_recall(
    reports: list[EvaluationReport],
    mode: str,
    category: str,
    value: float,
) -> None:
    report = next(item for item in reports if item.mode == mode)
    for case in report.cases:
        if case.category == category:
            case.recall_at_k = value


def compare(reports: list[EvaluationReport], policy: Stage3QualityPolicy):
    module = import_module("app.evaluation.comparison")
    return module.compare_stage3_reports(reports, policy)


def gate(comparison, gate_id: str):
    matches = [item for item in comparison.gates if item.gate_id == gate_id]
    assert len(matches) == 1
    return matches[0]


def test_compatible_reports_produce_deltas_categories_and_recommendation(
    policy: Stage3QualityPolicy,
) -> None:
    comparison = compare(make_four_reports(), policy)

    assert list(comparison.reports) == list(MODES)
    assert comparison.metric_deltas["vector"].recall_at_5 == 0.0
    assert comparison.metric_deltas["rewrite"].recall_at_5 == pytest.approx(0.13)
    assert comparison.category_recall["rewrite"]["multi_turn"] == 1.0
    assert comparison.recommended_mode == "rewrite"
    assert comparison.fallback_mode == "vector"
    assert comparison.passed is True


def test_rejects_missing_duplicate_or_unknown_mode(policy: Stage3QualityPolicy) -> None:
    reports = make_four_reports()
    with pytest.raises(ValueError, match="四种模式必须齐全且唯一"):
        compare(reports[:-1], policy)

    duplicate = make_four_reports()
    duplicate[-1].mode = "rerank"
    with pytest.raises(ValueError, match="四种模式必须齐全且唯一"):
        compare(duplicate, policy)

    unknown = make_four_reports()
    unknown[-1].mode = "unknown"
    with pytest.raises(ValueError, match="四种模式必须齐全且唯一"):
        compare(unknown, policy)


def test_rejects_legacy_report_with_regeneration_message(
    policy: Stage3QualityPolicy,
) -> None:
    reports = make_four_reports()
    reports[0].schema_version = "1.0"
    reports[0].provenance = None

    with pytest.raises(ValueError, match="重新生成 1.1 报告"):
        compare(reports, policy)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_sha256", "c" * 64),
        ("top_k", 6),
        ("case_count", 31),
    ],
)
def test_rejects_report_contract_mismatch(
    policy: Stage3QualityPolicy,
    field: str,
    value: object,
) -> None:
    reports = make_four_reports()
    setattr(reports[1], field, value)

    with pytest.raises(ValueError, match=field):
        compare(reports, policy)


def test_rejects_case_reorder_or_duplicate(policy: Stage3QualityPolicy) -> None:
    reordered = make_four_reports()
    reordered[1].cases.reverse()
    with pytest.raises(ValueError, match="案例 ID、分类和顺序"):
        compare(reordered, policy)

    duplicate = make_four_reports()
    duplicate[1].cases[-1].case_id = duplicate[1].cases[0].case_id
    with pytest.raises(ValueError, match="案例 ID 不得重复"):
        compare(duplicate, policy)


@pytest.mark.parametrize(
    "field",
    [
        "run_id",
        "knowledge_base_id",
        "snapshot_sha256",
        "document_count",
        "chunk_count",
    ],
)
def test_rejects_provenance_mismatch(
    policy: Stage3QualityPolicy,
    field: str,
) -> None:
    reports = make_four_reports()
    provenance = reports[1].provenance
    assert provenance is not None
    value: object = uuid4() if field.endswith("_id") else 99
    if field == "snapshot_sha256":
        value = "d" * 64
    setattr(provenance, field, value)

    with pytest.raises(ValueError, match=field):
        compare(reports, policy)


def test_generated_at_is_not_a_compatibility_field(policy: Stage3QualityPolicy) -> None:
    reports = make_four_reports()
    provenance = reports[1].provenance
    assert provenance is not None
    provenance.generated_at = datetime(2020, 1, 1, tzinfo=UTC)

    assert compare(reports, policy).passed is True


def test_rejects_common_environment_drift_without_echoing_value(
    policy: Stage3QualityPolicy,
) -> None:
    reports = make_four_reports()
    secret = "private-model-api_key=hidden"
    reports[1].environment["embedding_model"] = secret

    with pytest.raises(ValueError, match="embedding_model") as error:
        compare(reports, policy)
    assert secret not in str(error.value)


@pytest.mark.parametrize(
    ("mode", "field", "value"),
    [
        ("vector", "rag_retrieval_mode", "hybrid"),
        ("vector", "rag_reranker_provider", "local"),
        ("hybrid", "rag_reranker_provider", "local"),
        ("rerank", "rag_retrieval_mode", "vector"),
        ("rerank", "rag_reranker_provider", "disabled"),
        ("rewrite", "rag_reranker_allow_fallback", "True"),
    ],
)
def test_rejects_invalid_mode_environment(
    policy: Stage3QualityPolicy,
    mode: str,
    field: str,
    value: str,
) -> None:
    reports = make_four_reports()
    report = next(item for item in reports if item.mode == mode)
    report.environment[field] = value

    with pytest.raises(ValueError, match=field):
        compare(reports, policy)


def test_rejects_rerank_rewrite_environment_drift(
    policy: Stage3QualityPolicy,
) -> None:
    reports = make_four_reports()
    reports[3].environment["rag_candidate_k"] = "30"

    with pytest.raises(ValueError, match="rag_candidate_k"):
        compare(reports, policy)


def test_final_gate_only_uses_rewrite(policy: Stage3QualityPolicy) -> None:
    reports = make_four_reports()
    set_metric(reports, "vector", "recall_at_5", 0.50)
    set_metric(reports, "rewrite", "recall_at_5", 0.90)

    comparison = compare(reports, policy)

    assert gate(comparison, "stage3e.recall").status == "passed"


def test_stage3c_zero_gain_is_waived_when_citation_does_not_drop(
    policy: Stage3QualityPolicy,
) -> None:
    reports = make_four_reports()
    set_metric(reports, "hybrid", "mrr_at_5", 0.93)
    set_metric(reports, "rerank", "mrr_at_5", 0.93)
    set_metric(reports, "hybrid", "citation_hit_rate", 0.93)
    set_metric(reports, "rerank", "citation_hit_rate", 0.93)

    comparison = compare(reports, policy)
    result = gate(comparison, "stage3c.mrr_relative_gain")

    assert result.status == "waived"
    assert result.waiver is not None
    assert comparison.passed is True


@pytest.mark.parametrize(
    ("rerank_mrr", "rerank_citation"),
    [(0.92, 0.93), (0.93, 0.92)],
)
def test_stage3c_waiver_does_not_cover_regression(
    policy: Stage3QualityPolicy,
    rerank_mrr: float,
    rerank_citation: float,
) -> None:
    reports = make_four_reports()
    set_metric(reports, "hybrid", "mrr_at_5", 0.93)
    set_metric(reports, "rerank", "mrr_at_5", rerank_mrr)
    set_metric(reports, "hybrid", "citation_hit_rate", 0.93)
    set_metric(reports, "rerank", "citation_hit_rate", rerank_citation)

    comparison = compare(reports, policy)

    assert comparison.passed is False
    assert gate(comparison, "stage3c.mrr_relative_gain").status == "failed"


def test_stage3b_and_stage3d_use_ceiling_aware_targets(
    policy: Stage3QualityPolicy,
) -> None:
    reports = make_four_reports()
    set_category_recall(reports, "vector", "keyword", 1.0)
    set_category_recall(reports, "hybrid", "keyword", 1.0)
    set_category_recall(reports, "rerank", "multi_turn", 0.8333333333333334)
    set_category_recall(reports, "rewrite", "multi_turn", 1.0)

    comparison = compare(reports, policy)

    assert gate(comparison, "stage3b.keyword_recall").target == 1.0
    assert gate(comparison, "stage3d.multi_turn_recall").target == pytest.approx(0.9833333333333334)


@pytest.mark.parametrize(
    ("field", "gate_id", "value"),
    [
        ("recall_at_5", "stage3e.recall", 0.84),
        ("citation_hit_rate", "stage3e.citation", 0.89),
        ("refusal_accuracy", "stage3e.refusal", 0.89),
    ],
)
def test_rewrite_final_gate_failure(
    policy: Stage3QualityPolicy,
    field: str,
    gate_id: str,
    value: float,
) -> None:
    reports = make_four_reports()
    set_metric(reports, "rewrite", field, value)

    comparison = compare(reports, policy)

    assert gate(comparison, gate_id).status == "failed"
    assert comparison.passed is False


def test_failure_case_ids_only_include_case_ids(policy: Stage3QualityPolicy) -> None:
    reports = make_four_reports()
    rewrite = reports[3]
    rewrite.cases[0].recall_at_k = 0.0
    rewrite.cases[1].citation_hit_rate = 0.0
    rewrite.cases[2].refusal_correct = False

    comparison = compare(reports, policy)

    assert comparison.failure_case_ids == {
        "rewrite.recall": ["keyword-01"],
        "rewrite.citation": ["keyword-02"],
        "rewrite.refusal": ["keyword-03"],
    }

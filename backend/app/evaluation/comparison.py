from collections.abc import Sequence
from statistics import fmean
from typing import Literal

from pydantic import BaseModel

from app.evaluation.metrics import ceiling_aware_target, relative_gain
from app.evaluation.policy import QualityWaiver, Stage3QualityPolicy
from app.evaluation.runner import EvaluationMode
from app.evaluation.schemas import (
    EvaluationCategory,
    EvaluationReport,
    GateStatus,
)

MODES: tuple[EvaluationMode, ...] = ("vector", "hybrid", "rerank", "rewrite")
COMMON_ENV_KEYS = (
    "app_env",
    "embedding_provider",
    "embedding_model",
    "embedding_device",
    "embedding_batch_size",
    "chat_provider",
    "chat_model",
    "embedding_dimensions",
    "rag_score_threshold",
    "rag_rrf_rank_constant",
)
RERANK_ENV_KEYS = (
    "rag_reranker_provider",
    "rag_reranker_model",
    "rag_reranker_device",
    "rag_reranker_batch_size",
    "rag_candidate_k",
    "rag_reranker_allow_fallback",
    "rag_reranker_min_score",
)
PROVENANCE_FIELDS = (
    "run_id",
    "knowledge_base_id",
    "snapshot_sha256",
    "document_count",
    "chunk_count",
)


class MetricDelta(BaseModel):
    recall_at_5: float
    mrr_at_5: float
    citation_hit_rate: float
    refusal_accuracy: float
    latency_p50_ms: float
    latency_p95_ms: float


class GateResult(BaseModel):
    gate_id: str
    status: GateStatus
    actual: float | int | str
    target: float | int | str
    message: str
    waiver: QualityWaiver | None = None


class Stage3Comparison(BaseModel):
    reports: dict[EvaluationMode, EvaluationReport]
    metric_deltas: dict[EvaluationMode, MetricDelta]
    category_recall: dict[EvaluationMode, dict[EvaluationCategory, float]]
    gates: list[GateResult]
    failure_case_ids: dict[str, list[str]]
    recommended_mode: Literal["rewrite"] = "rewrite"
    fallback_mode: Literal["vector"] = "vector"
    passed: bool
    sanitized_failures: list[str]


def _index_reports(
    reports: Sequence[EvaluationReport],
) -> dict[EvaluationMode, EvaluationReport]:
    provided_modes = [report.mode for report in reports]
    if len(provided_modes) != len(MODES) or set(provided_modes) != set(MODES):
        raise ValueError("四种模式必须齐全且唯一")
    indexed = {report.mode: report for report in reports}
    return {mode: indexed[mode] for mode in MODES}


def _require_environment(
    report: EvaluationReport,
    key: str,
    expected: str,
) -> None:
    if report.environment.get(key) != expected:
        raise ValueError(f"{report.mode} 报告环境字段 {key} 不符合固定模式矩阵")


def _validate_compatibility(
    reports: dict[EvaluationMode, EvaluationReport],
) -> None:
    for mode in MODES:
        report = reports[mode]
        if report.schema_version != "1.1" or report.provenance is None:
            raise ValueError(f"{mode} 需要重新生成 1.1 报告")
        if report.case_count != len(report.cases):
            raise ValueError(f"{mode} 报告 case_count 与案例数量不一致")
        case_ids = [case.case_id for case in report.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError(f"{mode} 报告案例 ID 不得重复")

    baseline = reports["vector"]
    baseline_cases = [(case.case_id, case.category) for case in baseline.cases]
    baseline_provenance = baseline.provenance
    assert baseline_provenance is not None

    for mode in MODES[1:]:
        report = reports[mode]
        for field in ("dataset_sha256", "top_k", "case_count"):
            if getattr(report, field) != getattr(baseline, field):
                raise ValueError(f"{mode} 报告字段 {field} 与 vector 不一致")
        cases = [(case.case_id, case.category) for case in report.cases]
        if cases != baseline_cases:
            raise ValueError(f"{mode} 报告案例 ID、分类和顺序与 vector 不一致")
        provenance = report.provenance
        assert provenance is not None
        for field in PROVENANCE_FIELDS:
            if getattr(provenance, field) != getattr(baseline_provenance, field):
                raise ValueError(f"{mode} 报告溯源字段 {field} 与 vector 不一致")

    for key in COMMON_ENV_KEYS:
        if any(key not in reports[mode].environment for mode in MODES):
            raise ValueError(f"四份报告缺少公共环境字段 {key}")
        if len({reports[mode].environment[key] for mode in MODES}) != 1:
            raise ValueError(f"四份报告公共环境字段 {key} 不一致")

    _require_environment(reports["vector"], "rag_retrieval_mode", "vector")
    _require_environment(reports["vector"], "rag_reranker_provider", "disabled")
    _require_environment(reports["hybrid"], "rag_retrieval_mode", "hybrid")
    _require_environment(reports["hybrid"], "rag_reranker_provider", "disabled")
    for mode in ("rerank", "rewrite"):
        _require_environment(reports[mode], "rag_retrieval_mode", "hybrid")
        _require_environment(reports[mode], "rag_reranker_provider", "local")
        _require_environment(reports[mode], "rag_reranker_allow_fallback", "False")
    for key in RERANK_ENV_KEYS:
        if reports["rerank"].environment.get(key) != reports["rewrite"].environment.get(key):
            raise ValueError(f"rerank 与 rewrite 的环境字段 {key} 不一致")


def _category_recall(
    report: EvaluationReport,
) -> dict[EvaluationCategory, float]:
    grouped: dict[EvaluationCategory, list[float]] = {}
    for case in report.cases:
        if case.category is None:
            continue
        grouped.setdefault(case.category, []).append(case.recall_at_k)
    return {category: fmean(values) for category, values in grouped.items()}


def _metric_delta(
    report: EvaluationReport,
    baseline: EvaluationReport,
) -> MetricDelta:
    return MetricDelta(
        recall_at_5=report.recall_at_5 - baseline.recall_at_5,
        mrr_at_5=report.mrr_at_5 - baseline.mrr_at_5,
        citation_hit_rate=report.citation_hit_rate - baseline.citation_hit_rate,
        refusal_accuracy=report.refusal_accuracy - baseline.refusal_accuracy,
        latency_p50_ms=report.latency_p50_ms - baseline.latency_p50_ms,
        latency_p95_ms=report.latency_p95_ms - baseline.latency_p95_ms,
    )


def _threshold_gate(
    gate_id: str,
    *,
    actual: float | int,
    target: float | int,
) -> GateResult:
    passed = actual >= target
    return GateResult(
        gate_id=gate_id,
        status="passed" if passed else "failed",
        actual=actual,
        target=target,
        message=f"{gate_id} {'达到' if passed else '未达到'}目标",
    )


def _stage3c_mrr_gate(
    hybrid: EvaluationReport,
    rerank: EvaluationReport,
    policy: Stage3QualityPolicy,
) -> GateResult:
    actual = relative_gain(hybrid.mrr_at_5, rerank.mrr_at_5)
    target = policy.historical_thresholds.stage3c_mrr_relative_gain
    if actual >= target:
        return GateResult(
            gate_id="stage3c.mrr_relative_gain",
            status="passed",
            actual=actual,
            target=target,
            message="stage3c.mrr_relative_gain 达到目标",
        )

    waiver = next(item for item in policy.waivers if item.gate_id == "stage3c.mrr_relative_gain")
    waiver_applies = (
        actual >= waiver.minimum_allowed
        and rerank.mrr_at_5 >= hybrid.mrr_at_5
        and rerank.citation_hit_rate >= hybrid.citation_hit_rate
    )
    if waiver_applies:
        return GateResult(
            gate_id="stage3c.mrr_relative_gain",
            status="waived",
            actual=actual,
            target=target,
            message="质量门未通过、已获风险豁免",
            waiver=waiver,
        )
    return GateResult(
        gate_id="stage3c.mrr_relative_gain",
        status="failed",
        actual=actual,
        target=target,
        message="stage3c.mrr_relative_gain 未通过且豁免不适用",
    )


def _build_gates(
    reports: dict[EvaluationMode, EvaluationReport],
    category_recall: dict[EvaluationMode, dict[EvaluationCategory, float]],
    policy: Stage3QualityPolicy,
) -> list[GateResult]:
    vector = reports["vector"]
    hybrid = reports["hybrid"]
    rerank = reports["rerank"]
    rewrite = reports["rewrite"]
    present_categories = set(category_recall["vector"])
    keyword_target = ceiling_aware_target(
        category_recall["vector"].get("keyword", 0.0),
        policy.historical_thresholds.stage3b_keyword_gain,
    )
    multi_turn_target = ceiling_aware_target(
        category_recall["rerank"].get("multi_turn", 0.0),
        policy.historical_thresholds.stage3d_multi_turn_gain,
    )
    gates = [
        _threshold_gate(
            "stage3a.case_count",
            actual=vector.case_count,
            target=policy.minimum_case_count,
        ),
        _threshold_gate(
            "stage3a.category_coverage",
            actual=len(present_categories & set(policy.required_categories)),
            target=len(policy.required_categories),
        ),
        _threshold_gate(
            "stage3b.overall_recall",
            actual=hybrid.recall_at_5,
            target=vector.recall_at_5,
        ),
        _threshold_gate(
            "stage3b.keyword_recall",
            actual=category_recall["hybrid"].get("keyword", 0.0),
            target=keyword_target,
        ),
        _threshold_gate(
            "stage3b.citation",
            actual=hybrid.citation_hit_rate,
            target=vector.citation_hit_rate,
        ),
        _threshold_gate(
            "stage3b.refusal",
            actual=hybrid.refusal_accuracy,
            target=vector.refusal_accuracy,
        ),
        _stage3c_mrr_gate(hybrid, rerank, policy),
        _threshold_gate(
            "stage3c.citation",
            actual=rerank.citation_hit_rate,
            target=hybrid.citation_hit_rate,
        ),
        _threshold_gate(
            "stage3d.multi_turn_recall",
            actual=category_recall["rewrite"].get("multi_turn", 0.0),
            target=multi_turn_target,
        ),
        _threshold_gate(
            "stage3e.recall",
            actual=rewrite.recall_at_5,
            target=policy.final_thresholds.recall_at_5,
        ),
        _threshold_gate(
            "stage3e.citation",
            actual=rewrite.citation_hit_rate,
            target=policy.final_thresholds.citation_hit_rate,
        ),
        _threshold_gate(
            "stage3e.refusal",
            actual=rewrite.refusal_accuracy,
            target=policy.final_thresholds.refusal_accuracy,
        ),
    ]
    return gates


def _failure_case_ids(
    rewrite: EvaluationReport,
) -> dict[str, list[str]]:
    return {
        "rewrite.recall": [case.case_id for case in rewrite.cases if case.recall_at_k < 1.0],
        "rewrite.citation": [
            case.case_id
            for case in rewrite.cases
            if case.citation_hit_rate is not None and case.citation_hit_rate < 1.0
        ],
        "rewrite.refusal": [case.case_id for case in rewrite.cases if not case.refusal_correct],
    }


def compare_stage3_reports(
    reports: Sequence[EvaluationReport],
    policy: Stage3QualityPolicy,
) -> Stage3Comparison:
    indexed = _index_reports(reports)
    _validate_compatibility(indexed)
    category_recall = {mode: _category_recall(indexed[mode]) for mode in MODES}
    baseline = indexed["vector"]
    metric_deltas = {mode: _metric_delta(indexed[mode], baseline) for mode in MODES}
    gates = _build_gates(indexed, category_recall, policy)
    passed = all(gate.status != "failed" for gate in gates)
    return Stage3Comparison(
        reports=indexed,
        metric_deltas=metric_deltas,
        category_recall=category_recall,
        gates=gates,
        failure_case_ids=_failure_case_ids(indexed["rewrite"]),
        passed=passed,
        sanitized_failures=[
            f"{gate.gate_id}：{gate.message}" for gate in gates if gate.status == "failed"
        ],
    )

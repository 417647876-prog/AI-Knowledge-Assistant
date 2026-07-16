"""导入不含案例正文的阶段 3 离线评测摘要。"""

import argparse
import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.event_loop import new_event_loop
from app.db.models import QualityEvaluationRun
from app.db.session import session_factory
from app.evaluation.policy import Stage3QualityPolicy, load_stage3_quality_policy
from app.evaluation.schemas import CaseResult, EvaluationProvenance, EvaluationReport

SAFE_ENVIRONMENT_KEYS = frozenset(
    {
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
        "rag_retrieval_mode",
        "rag_reranker_provider",
        "rag_reranker_model",
        "rag_reranker_device",
        "rag_reranker_batch_size",
        "rag_candidate_k",
        "rag_reranker_allow_fallback",
        "rag_reranker_min_score",
    }
)


@dataclass(frozen=True)
class QualityEvaluationSummary:
    dataset_hash: str
    mode: str
    model_config_summary: dict[str, str]
    metrics: dict[str, int | float]
    report_hash: str
    gate_passed: bool
    started_at: datetime
    completed_at: datetime
    duration_ms: int


def _reject_unknown_report_fields(raw: object) -> None:
    if not isinstance(raw, dict):
        return
    unknown = set(raw) - set(EvaluationReport.model_fields)
    if unknown:
        raise ValueError(f"离线评测报告包含未知字段：{sorted(unknown)}")
    provenance = raw.get("provenance")
    if isinstance(provenance, dict):
        unknown = set(provenance) - set(EvaluationProvenance.model_fields)
        if unknown:
            raise ValueError(f"离线评测报告 provenance 包含未知字段：{sorted(unknown)}")
    cases = raw.get("cases")
    if isinstance(cases, list):
        allowed_case_fields = set(CaseResult.model_fields)
        for item in cases:
            if not isinstance(item, dict):
                continue
            unknown = set(item) - allowed_case_fields
            if unknown:
                raise ValueError(f"离线评测报告 case 包含未知字段：{sorted(unknown)}")


def parse_quality_evaluation(
    report_bytes: bytes,
    policy: Stage3QualityPolicy,
) -> QualityEvaluationSummary:
    raw: Any = json.loads(report_bytes)
    _reject_unknown_report_fields(raw)
    report = EvaluationReport.model_validate(raw)
    if report.schema_version != "1.1":
        raise ValueError("离线评测报告必须使用 schema 1.1")
    if report.mode != policy.final_mode:
        raise ValueError("离线评测报告模式与质量策略 final_mode 不一致")
    if report.case_count < policy.minimum_case_count:
        raise ValueError("离线评测报告案例数低于质量策略要求")
    categories = {case.category for case in report.cases if case.category is not None}
    if categories != set(policy.required_categories):
        raise ValueError("离线评测报告未完整覆盖质量策略要求的分类")

    completed_at = report.provenance.generated_at
    duration_ms = round(sum(case.latency_ms for case in report.cases))
    metrics: dict[str, int | float] = {
        "case_count": report.case_count,
        "top_k": report.top_k,
        "recall_at_5": report.recall_at_5,
        "mrr_at_5": report.mrr_at_5,
        "citation_hit_rate": report.citation_hit_rate,
        "refusal_accuracy": report.refusal_accuracy,
        "latency_p50_ms": report.latency_p50_ms,
        "latency_p95_ms": report.latency_p95_ms,
    }
    gate_passed = (
        report.recall_at_5 >= policy.final_thresholds.recall_at_5
        and report.citation_hit_rate >= policy.final_thresholds.citation_hit_rate
        and report.refusal_accuracy >= policy.final_thresholds.refusal_accuracy
    )
    return QualityEvaluationSummary(
        dataset_hash=report.dataset_sha256,
        mode=report.mode,
        model_config_summary={
            key: value for key, value in report.environment.items() if key in SAFE_ENVIRONMENT_KEYS
        },
        metrics=metrics,
        report_hash=hashlib.sha256(report_bytes).hexdigest(),
        gate_passed=gate_passed,
        started_at=completed_at - timedelta(milliseconds=duration_ms),
        completed_at=completed_at,
        duration_ms=duration_ms,
    )


async def record_quality_evaluation(
    session: AsyncSession,
    summary: QualityEvaluationSummary,
) -> QualityEvaluationRun:
    statement = (
        pg_insert(QualityEvaluationRun)
        .values(
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
        .on_conflict_do_nothing(index_elements=[QualityEvaluationRun.report_hash])
        .returning(QualityEvaluationRun)
    )
    run = await session.scalar(statement)
    if run is not None:
        return run
    existing = await session.scalar(
        select(QualityEvaluationRun).where(QualityEvaluationRun.report_hash == summary.report_hash)
    )
    if existing is None:
        raise RuntimeError("离线评测摘要幂等写入失败")
    return existing


def _find_repo_root(path: Path) -> Path:
    resolved = path.resolve()
    for candidate in (resolved.parent, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    return resolved.parent


async def import_quality_evaluation(
    report_path: Path,
    policy_path: Path,
) -> QualityEvaluationRun:
    report_bytes = report_path.read_bytes()
    policy = load_stage3_quality_policy(
        policy_path,
        repo_root=_find_repo_root(policy_path),
    )
    summary = parse_quality_evaluation(report_bytes, policy)
    async with session_factory.begin() as session:
        return await record_quality_evaluation(session, summary)


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入脱敏的阶段 3 离线评测摘要")
    parser.add_argument("--report", type=Path, required=True, help="阶段 3 评测 JSON 报告")
    parser.add_argument("--policy", type=Path, required=True, help="版本化质量策略 JSON")
    return parser.parse_args(arguments)


def run_import(report_path: Path, policy_path: Path) -> QualityEvaluationRun:
    with asyncio.Runner(loop_factory=new_event_loop) as runner:
        return runner.run(import_quality_evaluation(report_path, policy_path))


def main(arguments: list[str] | None = None) -> None:
    args = parse_args(arguments)
    try:
        run = run_import(args.report, args.policy)
    except Exception as error:
        raise SystemExit(f"离线评测摘要导入失败：{type(error).__name__}") from None
    print(f"离线评测摘要已记录：id={run.id}, report_hash={run.report_hash}")


if __name__ == "__main__":
    main()

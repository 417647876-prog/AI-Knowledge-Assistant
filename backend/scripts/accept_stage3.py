"""顺序执行四种模式并生成阶段 3 综合质量验收产物。"""

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.event_loop import new_event_loop
from app.db.session import session_factory
from app.evaluation.comparison import Stage3Comparison, compare_stage3_reports
from app.evaluation.policy import load_stage3_quality_policy
from app.evaluation.reporting import ensure_public_report_safe, render_stage3_markdown
from app.evaluation.runner import EvaluationMode
from app.evaluation.schemas import EvaluationReport, Stage3AcceptanceManifest
from app.evaluation.snapshot import KnowledgeBaseSnapshot, compute_knowledge_base_snapshot
from scripts.evaluate_rag import _top_k_at_least_five, run_from_args

MODES: tuple[EvaluationMode, ...] = ("vector", "hybrid", "rerank", "rewrite")
REPORT_NAMES: dict[EvaluationMode, str] = {
    "vector": "stage3e-vector.json",
    "hybrid": "stage3e-hybrid.json",
    "rerank": "stage3e-rerank.json",
    "rewrite": "stage3e-rewrite.json",
}
MANIFEST_NAME = "stage3e-manifest.json"
MARKDOWN_ARTIFACT_NAME = "docs/阶段3质量验收报告.md"


@dataclass(frozen=True)
class AcceptanceRun:
    comparison: Stage3Comparison
    manifest: Stage3AcceptanceManifest
    report_paths: dict[EvaluationMode, Path]
    markdown_path: Path
    manifest_path: Path


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="执行阶段 3 RAG 综合质量验收")
    parser.add_argument("--dataset", type=Path, required=True, help="固定评估 JSONL 数据集")
    parser.add_argument("--knowledge-base-id", type=UUID, required=True, help="目标知识库 UUID")
    parser.add_argument("--policy", type=Path, required=True, help="版本化质量策略 JSON")
    parser.add_argument(
        "--reports-dir",
        type=Path,
        required=True,
        help="四模式报告和 manifest 目录",
    )
    parser.add_argument(
        "--markdown-output",
        type=Path,
        required=True,
        help="脱敏中文验收报告路径",
    )
    parser.add_argument(
        "--top-k",
        type=_top_k_at_least_five,
        default=5,
        help="检索候选数量，至少为 5",
    )
    return parser.parse_args(arguments)


def _find_policy_repo_root(policy_path: Path) -> Path:
    resolved = policy_path.resolve()
    for candidate in (resolved.parent, *resolved.parents):
        if (candidate / ".git").exists():
            return candidate
    return resolved.parent


def _validate_paths(args: argparse.Namespace) -> None:
    if not args.dataset.is_file():
        raise FileNotFoundError("评估数据集不存在")
    if not args.policy.is_file():
        raise FileNotFoundError("质量策略不存在")
    if args.reports_dir.exists() and not args.reports_dir.is_dir():
        raise ValueError("报告目录不是文件夹")
    if args.markdown_output.parent.exists() and not args.markdown_output.parent.is_dir():
        raise ValueError("Markdown 输出目录不是文件夹")

    report_paths = [args.reports_dir / REPORT_NAMES[mode] for mode in MODES]
    reserved_paths = [*report_paths, args.reports_dir / MANIFEST_NAME]
    markdown_path = args.markdown_output.resolve()
    if any(markdown_path == path.resolve() for path in reserved_paths):
        raise ValueError("Markdown 输出不能与 JSON 或 manifest 使用同一路径")


async def compute_baseline_snapshot(knowledge_base_id: UUID) -> KnowledgeBaseSnapshot:
    async with session_factory() as session:
        return await compute_knowledge_base_snapshot(session, knowledge_base_id)


def _mode_args(args: argparse.Namespace, mode: EvaluationMode) -> argparse.Namespace:
    return argparse.Namespace(
        dataset=args.dataset,
        knowledge_base_id=args.knowledge_base_id,
        mode=mode,
        output=args.reports_dir / REPORT_NAMES[mode],
        top_k=args.top_k,
    )


def _powershell_quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _reproduce_command(args: argparse.Namespace) -> str:
    return " ".join(
        [
            "uv run python -m scripts.accept_stage3",
            "--dataset",
            _powershell_quote(args.dataset),
            "--knowledge-base-id",
            "$env:STAGE3_KNOWLEDGE_BASE_ID",
            "--policy",
            _powershell_quote(args.policy),
            "--reports-dir",
            _powershell_quote(args.reports_dir),
            "--markdown-output",
            _powershell_quote(args.markdown_output),
            "--top-k",
            str(args.top_k),
        ]
    )


def _serialize_report(report: EvaluationReport) -> str:
    return json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def write_acceptance_bundle(
    comparison: Stage3Comparison,
    markdown: str,
    *,
    reports_dir: Path,
    markdown_output: Path,
) -> AcceptanceRun:
    ensure_public_report_safe(markdown)
    serialized_reports = {mode: _serialize_report(comparison.reports[mode]) for mode in MODES}
    for serialized in serialized_reports.values():
        ensure_public_report_safe(serialized)

    reports_dir.mkdir(parents=True, exist_ok=True)
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    report_paths = {mode: reports_dir / REPORT_NAMES[mode] for mode in MODES}
    manifest_path = reports_dir / MANIFEST_NAME

    with TemporaryDirectory(prefix=".stage3e-", dir=reports_dir) as temp_name:
        temp_dir = Path(temp_name)
        staged_reports: dict[EvaluationMode, Path] = {}
        artifacts: dict[str, str] = {}
        for mode in MODES:
            content = serialized_reports[mode].encode("utf-8")
            staged_path = temp_dir / REPORT_NAMES[mode]
            staged_path.write_bytes(content)
            staged_reports[mode] = staged_path
            artifacts[REPORT_NAMES[mode]] = _sha256(content)

        markdown_content = markdown.encode("utf-8")
        staged_markdown = temp_dir / "stage3e-acceptance.md"
        staged_markdown.write_bytes(markdown_content)
        artifacts[MARKDOWN_ARTIFACT_NAME] = _sha256(markdown_content)

        provenance = comparison.reports["rewrite"].provenance
        if provenance is None:
            raise ValueError("rewrite 报告缺少溯源信息")
        manifest = Stage3AcceptanceManifest(
            run_id=provenance.run_id,
            snapshot_sha256=provenance.snapshot_sha256,
            artifacts=artifacts,
            gate_statuses={gate.gate_id: gate.status for gate in comparison.gates},
            passed=comparison.passed,
        )
        manifest_content = (
            json.dumps(manifest.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
        ).encode("utf-8")
        staged_manifest = temp_dir / MANIFEST_NAME
        staged_manifest.write_bytes(manifest_content)

        for mode in MODES:
            os.replace(staged_reports[mode], report_paths[mode])
        os.replace(staged_markdown, markdown_output)
        os.replace(staged_manifest, manifest_path)

    return AcceptanceRun(
        comparison=comparison,
        manifest=manifest,
        report_paths=report_paths,
        markdown_path=markdown_output,
        manifest_path=manifest_path,
    )


async def run_acceptance(
    args: argparse.Namespace,
    settings: Settings,
) -> AcceptanceRun:
    _validate_paths(args)
    policy = load_stage3_quality_policy(
        args.policy,
        repo_root=_find_policy_repo_root(args.policy),
    )
    run_id = uuid4()
    baseline_snapshot = await compute_baseline_snapshot(args.knowledge_base_id)
    reports = []
    for mode in MODES:
        reports.append(
            await run_from_args(
                _mode_args(args, mode),
                settings,
                run_id=run_id,
                expected_snapshot=baseline_snapshot,
            )
        )
    comparison = compare_stage3_reports(reports, policy)
    markdown = render_stage3_markdown(
        comparison,
        reproduce_command=_reproduce_command(args),
    )
    return write_acceptance_bundle(
        comparison,
        markdown,
        reports_dir=args.reports_dir,
        markdown_output=args.markdown_output,
    )


def run_acceptance_command(
    args: argparse.Namespace,
    settings: Settings,
) -> AcceptanceRun:
    with asyncio.Runner(loop_factory=new_event_loop) as runner:
        return runner.run(run_acceptance(args, settings))


def format_acceptance_error(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return "验收失败：输入文件不存在。"
    if isinstance(error, ValidationError):
        return "验收失败：报告或策略 schema 校验失败。"
    if isinstance(error, ValueError):
        return "验收失败：输入、快照或报告兼容性校验失败。"
    if isinstance(error, OSError):
        return "验收失败：产物写入失败。"
    return f"验收失败：{type(error).__name__}。请查看本地日志并检查运行环境。"


def _print_summary(run: AcceptanceRun) -> None:
    rewrite = run.comparison.reports["rewrite"]
    print(f"阶段 3 验收运行 ID：{run.manifest.run_id}")
    for mode in MODES:
        print(f"{mode} 报告：{run.report_paths[mode]}")
    print(f"中文报告：{run.markdown_path}")
    print(
        "rewrite 指标："
        f"Recall@5={rewrite.recall_at_5 * 100:.2f}%，"
        f"引用={rewrite.citation_hit_rate * 100:.2f}%，"
        f"拒答={rewrite.refusal_accuracy * 100:.2f}%"
    )
    print("质量门：" + "，".join(f"{gate.gate_id}={gate.status}" for gate in run.comparison.gates))


def main(arguments: list[str] | None = None) -> int:
    args = parse_args(arguments)
    try:
        run = run_acceptance_command(args, get_settings())
    except Exception as error:
        print(format_acceptance_error(error))
        return 1

    _print_summary(run)
    if run.comparison.passed:
        return 0
    for failure in run.comparison.sanitized_failures:
        print(f"未通过：{failure}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

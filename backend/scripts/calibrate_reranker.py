"""生成独立的 Reranker 接受阈值校准报告。"""

import argparse
import asyncio
import hashlib
import json
from math import isfinite
from pathlib import Path
from tempfile import NamedTemporaryFile

from app.ai.contracts import RerankerProvider
from app.ai.rerankers import get_local_reranker_provider
from app.evaluation.reranker_calibration import (
    CalibrationReport,
    load_calibration_cases,
    select_acceptance_threshold,
)


def _batch_size(value: str) -> int:
    try:
        batch_size = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("batch-size 必须是整数") from error
    if not 1 <= batch_size <= 256:
        raise argparse.ArgumentTypeError("batch-size 必须位于 1 到 256 之间")
    return batch_size


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="校准 Reranker 接受阈值")
    parser.add_argument("--dataset", type=Path, required=True, help="校准 JSONL 数据集路径")
    parser.add_argument("--output", type=Path, required=True, help="校准 JSON 报告输出路径")
    parser.add_argument(
        "--model",
        default="BAAI/bge-reranker-base",
        help="本地 CrossEncoder 模型名称",
    )
    parser.add_argument(
        "--device",
        choices=["auto", "cuda", "cpu"],
        default="cpu",
        help="模型运行设备",
    )
    parser.add_argument(
        "--batch-size",
        type=_batch_size,
        default=16,
        help="批量推理大小，范围 1..256",
    )
    return parser.parse_args(arguments)


async def run_calibration(
    *,
    dataset: Path,
    model_name: str,
    device: str,
    provider: RerankerProvider,
) -> CalibrationReport:
    dataset_sha256 = hashlib.sha256(dataset.read_bytes()).hexdigest()
    cases = load_calibration_cases(dataset)

    grouped_cases: dict[str, list[tuple[int, str]]] = {}
    for index, case in enumerate(cases):
        grouped_cases.setdefault(case.question, []).append((index, case.document))

    restored_scores = [0.0] * len(cases)
    for question, indexed_documents in grouped_cases.items():
        documents = [document for _, document in indexed_documents]
        raw_scores = await provider.rerank(question, documents)
        if len(raw_scores) != len(documents):
            raise ValueError("Reranker 返回的分数数量与文档数量不一致")
        try:
            scores = [float(score) for score in raw_scores]
        except (TypeError, ValueError):
            raise ValueError("Reranker 返回的分数必须为有限数值") from None
        if not all(isfinite(score) for score in scores):
            raise ValueError("Reranker 返回的分数必须为有限数值")
        for (original_index, _), score in zip(indexed_documents, scores, strict=True):
            restored_scores[original_index] = score

    return select_acceptance_threshold(
        cases,
        restored_scores,
        model_name=model_name,
        device=device,
        dataset_sha256=dataset_sha256,
    )


def write_report(report: CalibrationReport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(
                report.model_dump(mode="json"),
                temporary_file,
                ensure_ascii=False,
                indent=2,
            )
            temporary_file.write("\n")
        temporary_path.replace(output)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def format_safe_error(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return "校准失败：数据集文件不存在。"
    if isinstance(error, ValueError):
        return "校准失败：校准参数、数据集或评分结果无效。"
    return f"校准失败：{type(error).__name__}。请检查本地日志与运行配置。"


def main(arguments: list[str] | None = None) -> None:
    args = parse_args(arguments)
    try:
        args.output.unlink(missing_ok=True)
        provider = get_local_reranker_provider(args.model, args.device, args.batch_size)
        report = asyncio.run(
            run_calibration(
                dataset=args.dataset,
                model_name=args.model,
                device=args.device,
                provider=provider,
            )
        )
        write_report(report, args.output)
    except Exception as error:
        raise SystemExit(format_safe_error(error)) from None
    print(f"校准完成：已输出 {args.output}。")


if __name__ == "__main__":
    main()

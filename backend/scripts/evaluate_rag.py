"""运行阶段 3 的可复现 RAG 检索评估。"""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from app.ai.contracts import EmbeddingProvider
from app.api.v1.questions import (
    get_question_chat_provider,
    get_question_embedding_provider,
    get_question_rewriter,
)
from app.core.config import Settings, get_settings
from app.db.session import session_factory
from app.evaluation.dataset import load_evaluation_cases
from app.evaluation.runner import EvaluationAnswerer, EvaluationRetriever, evaluate_cases
from app.evaluation.schemas import EvaluationCase, EvaluationReport
from app.rag.retriever import VectorRetriever
from app.rag.schemas import QuestionAnswer
from app.rag.service import RagService


class RagServiceEvaluationAnswerer:
    """把已有 RagService 适配为评估 Runner 所需的回答接口。"""

    def __init__(self, service: RagService) -> None:
        self._service = service

    async def answer_case(
        self, *, knowledge_base_id: UUID, case: EvaluationCase, top_k: int
    ) -> QuestionAnswer:
        return await self._service.answer(knowledge_base_id, case.question, top_k)


def _top_k_at_least_five(value: str) -> int:
    try:
        top_k = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("top_k 必须是整数") from error
    if top_k < 5:
        raise argparse.ArgumentTypeError("top_k 不能小于 5，否则无法计算 Recall@5")
    return top_k


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 RAG 纯向量检索基线报告")
    parser.add_argument("--dataset", type=Path, required=True, help="评估 JSONL 数据集路径")
    parser.add_argument("--knowledge-base-id", type=UUID, required=True, help="目标知识库 UUID")
    parser.add_argument("--mode", choices=["vector"], required=True, help="当前只支持纯向量检索")
    parser.add_argument("--output", type=Path, required=True, help="JSON 报告输出路径")
    parser.add_argument(
        "--top-k", type=_top_k_at_least_five, default=5, help="检索候选数量，至少为 5"
    )
    return parser.parse_args(arguments)


def build_safe_environment(settings: Settings) -> dict[str, str]:
    """返回可复现但不含连接信息或密钥的运行环境摘要。"""
    return {
        "app_env": settings.app_env,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "chat_provider": settings.chat_provider,
        "chat_model": settings.chat_model,
        "embedding_dimensions": str(settings.embedding_dimensions),
        "rag_score_threshold": str(settings.rag_score_threshold),
    }


def write_report(report: EvaluationReport, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def format_safe_error(error: Exception) -> str:
    if isinstance(error, FileNotFoundError):
        return "评估失败：数据集文件不存在。"
    if isinstance(error, ValueError):
        return "评估失败：评估参数或数据集格式无效。"
    return f"评估失败：{type(error).__name__}。请查看本地日志并确认运行配置。"


async def run_evaluation(
    *,
    dataset: Path,
    knowledge_base_id: UUID,
    settings: Settings,
    embedding_provider: EmbeddingProvider,
    retriever: EvaluationRetriever,
    answerer: EvaluationAnswerer,
    top_k: int,
) -> EvaluationReport:
    return await evaluate_cases(
        cases=load_evaluation_cases(dataset),
        knowledge_base_id=knowledge_base_id,
        embedding_provider=embedding_provider,
        retriever=retriever,
        answerer=answerer,
        top_k=top_k,
        score_threshold=settings.rag_score_threshold,
        mode="vector",
        environment=build_safe_environment(settings),
    )


async def run_from_args(args: argparse.Namespace, settings: Settings) -> EvaluationReport:
    embedding_dependency = get_question_embedding_provider(settings)
    chat_dependency = get_question_chat_provider(settings)
    try:
        embedding_provider = await anext(embedding_dependency)
        chat_provider = await anext(chat_dependency)
        question_rewriter = await get_question_rewriter(settings, chat_provider)
        async with session_factory() as session:
            retriever = VectorRetriever(session)
            service = RagService(
                session=session,
                embedding_provider=embedding_provider,
                retriever=retriever,
                chat_provider=chat_provider,
                question_rewriter=question_rewriter,
                score_threshold=settings.rag_score_threshold,
            )
            return await run_evaluation(
                dataset=args.dataset,
                knowledge_base_id=args.knowledge_base_id,
                settings=settings,
                embedding_provider=embedding_provider,
                retriever=retriever,
                answerer=RagServiceEvaluationAnswerer(service),
                top_k=args.top_k,
            )
    finally:
        await chat_dependency.aclose()
        await embedding_dependency.aclose()


def main(arguments: list[str] | None = None) -> None:
    args = parse_args(arguments)
    try:
        report = asyncio.run(run_from_args(args, get_settings()))
        write_report(report, args.output)
    except Exception as error:
        raise SystemExit(format_safe_error(error)) from None
    print(f"评估完成：已输出 {args.output}。")


if __name__ == "__main__":
    main()

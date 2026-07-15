"""运行阶段 3 的可复现 RAG 检索评估。"""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import UUID

from app.ai.contracts import EmbeddingProvider
from app.api.v1.questions import (
    build_retriever,
    get_question_chat_provider,
    get_question_embedding_provider,
    get_question_reranker,
    get_question_rewriter,
)
from app.core.config import Settings, get_settings
from app.core.event_loop import new_event_loop
from app.db.session import session_factory
from app.evaluation.dataset import load_evaluation_cases
from app.evaluation.runner import (
    EvaluationAnswerer,
    EvaluationAnswerResult,
    EvaluationMode,
    EvaluationRetriever,
    evaluate_cases,
)
from app.evaluation.schemas import EvaluationCase, EvaluationReport
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

    async def answer_case_with_retrieval(
        self, *, knowledge_base_id: UUID, case: EvaluationCase, top_k: int
    ) -> EvaluationAnswerResult:
        answer, chunks, retrieval_latency_ms = await self._service.answer_with_retrieval(
            knowledge_base_id,
            case.question,
            top_k,
        )
        return EvaluationAnswerResult(
            answer=answer,
            retrieved_chunks=chunks,
            retrieval_latency_ms=retrieval_latency_ms,
        )


def _top_k_at_least_five(value: str) -> int:
    try:
        top_k = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("top_k 必须是整数") from error
    if top_k < 5:
        raise argparse.ArgumentTypeError("top_k 不能小于 5，否则无法计算 Recall@5")
    return top_k


def parse_args(arguments: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="生成 RAG 检索评估报告")
    parser.add_argument("--dataset", type=Path, required=True, help="评估 JSONL 数据集路径")
    parser.add_argument("--knowledge-base-id", type=UUID, required=True, help="目标知识库 UUID")
    parser.add_argument(
        "--mode",
        choices=["vector", "hybrid", "rerank"],
        required=True,
        help="选择纯向量、混合检索或本地重排序",
    )
    parser.add_argument("--output", type=Path, required=True, help="JSON 报告输出路径")
    parser.add_argument(
        "--top-k", type=_top_k_at_least_five, default=5, help="检索候选数量，至少为 5"
    )
    return parser.parse_args(arguments)


def build_evaluation_settings(settings: Settings, mode: EvaluationMode) -> Settings:
    """把评估模式转换为可复现的检索与重排序配置。"""
    if mode == "rerank":
        return settings.model_copy(
            update={
                "rag_retrieval_mode": "hybrid",
                "rag_reranker_provider": "local",
                "rag_reranker_allow_fallback": False,
            }
        )
    return settings.model_copy(
        update={
            "rag_retrieval_mode": mode,
            "rag_reranker_provider": "disabled",
        }
    )


def build_safe_environment(settings: Settings) -> dict[str, str]:
    """返回可复现但不含连接信息或密钥的运行环境摘要。"""
    return {
        "app_env": settings.app_env,
        "embedding_provider": settings.embedding_provider,
        "embedding_model": settings.embedding_model,
        "embedding_device": settings.embedding_device,
        "embedding_batch_size": str(settings.embedding_batch_size),
        "chat_provider": settings.chat_provider,
        "chat_model": settings.chat_model,
        "embedding_dimensions": str(settings.embedding_dimensions),
        "rag_score_threshold": str(settings.rag_score_threshold),
        "rag_retrieval_mode": settings.rag_retrieval_mode,
        "rag_rrf_rank_constant": str(settings.rag_rrf_rank_constant),
        "rag_reranker_provider": settings.rag_reranker_provider,
        "rag_reranker_model": settings.rag_reranker_model,
        "rag_reranker_device": settings.rag_reranker_device,
        "rag_reranker_batch_size": str(settings.rag_reranker_batch_size),
        "rag_candidate_k": str(settings.rag_candidate_k),
        "rag_reranker_allow_fallback": str(settings.rag_reranker_allow_fallback),
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
    mode: EvaluationMode,
) -> EvaluationReport:
    return await evaluate_cases(
        cases=load_evaluation_cases(dataset),
        knowledge_base_id=knowledge_base_id,
        embedding_provider=embedding_provider,
        retriever=retriever,
        answerer=answerer,
        top_k=top_k,
        score_threshold=settings.rag_score_threshold,
        mode=mode,
        environment=build_safe_environment(settings),
    )


async def run_from_args(args: argparse.Namespace, settings: Settings) -> EvaluationReport:
    evaluation_settings = build_evaluation_settings(settings, args.mode)
    embedding_dependency = get_question_embedding_provider(evaluation_settings)
    chat_dependency = get_question_chat_provider(evaluation_settings)
    try:
        embedding_provider = await anext(embedding_dependency)
        chat_provider = await anext(chat_dependency)
        question_rewriter = await get_question_rewriter(evaluation_settings, chat_provider)
        reranker = get_question_reranker(evaluation_settings)
        async with session_factory() as session:
            retriever = build_retriever(session, evaluation_settings)
            service = RagService(
                session=session,
                embedding_provider=embedding_provider,
                retriever=retriever,
                chat_provider=chat_provider,
                question_rewriter=question_rewriter,
                score_threshold=evaluation_settings.rag_score_threshold,
                reranker=reranker,
                candidate_k=evaluation_settings.rag_candidate_k,
                reranker_allow_fallback=evaluation_settings.rag_reranker_allow_fallback,
            )
            return await run_evaluation(
                dataset=args.dataset,
                knowledge_base_id=args.knowledge_base_id,
                settings=evaluation_settings,
                embedding_provider=embedding_provider,
                retriever=retriever,
                answerer=RagServiceEvaluationAnswerer(service),
                top_k=args.top_k,
                mode=args.mode,
            )
    finally:
        await chat_dependency.aclose()
        await embedding_dependency.aclose()


def run_evaluation_command(args: argparse.Namespace, settings: Settings) -> EvaluationReport:
    """使用 psycopg 在 Windows 支持的 SelectorEventLoop 运行评估。"""
    with asyncio.Runner(loop_factory=new_event_loop) as runner:
        return runner.run(run_from_args(args, settings))


def main(arguments: list[str] | None = None) -> None:
    args = parse_args(arguments)
    try:
        report = run_evaluation_command(args, get_settings())
        write_report(report, args.output)
    except Exception as error:
        raise SystemExit(format_safe_error(error)) from None
    print(f"评估完成：已输出 {args.output}。")


if __name__ == "__main__":
    main()

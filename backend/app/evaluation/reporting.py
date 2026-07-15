from collections.abc import Iterable

from app.evaluation.comparison import MODES, GateResult, Stage3Comparison
from app.evaluation.schemas import EvaluationCategory, EvaluationReport

CATEGORY_ORDER: tuple[EvaluationCategory, ...] = (
    "keyword",
    "semantic",
    "refusal",
    "multi_turn",
    "interference",
)
FAILURE_GROUP_ORDER = (
    "rewrite.recall",
    "rewrite.citation",
    "rewrite.refusal",
)
SAFE_ENVIRONMENT_KEYS = (
    "app_env",
    "embedding_provider",
    "embedding_model",
    "embedding_device",
    "embedding_batch_size",
    "chat_provider",
    "chat_model",
    "embedding_dimensions",
    "rag_score_threshold",
    "rag_retrieval_mode",
    "rag_rrf_rank_constant",
    "rag_reranker_provider",
    "rag_reranker_model",
    "rag_reranker_device",
    "rag_reranker_batch_size",
    "rag_candidate_k",
    "rag_reranker_allow_fallback",
    "rag_reranker_min_score",
)
SENSITIVE_MARKERS = (
    "database_url",
    "api_key",
    "access_token",
    "postgresql://",
    "postgresql+psycopg://",
)
STATUS_LABELS = {
    "passed": "通过",
    "failed": "未通过",
    "waived": "已豁免",
}
RATE_GATE_IDS = {
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
}


def _percent(value: float) -> str:
    return f"{value * 100:.2f}%"


def _signed_percent(value: float) -> str:
    return f"{value * 100:+.2f}%"


def _milliseconds(value: float) -> str:
    return f"{value:.2f} ms"


def _signed_milliseconds(value: float) -> str:
    return f"{value:+.2f} ms"


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _table(headers: Iterable[str], rows: Iterable[Iterable[object]]) -> list[str]:
    header_values = list(headers)
    lines = [
        "| " + " | ".join(header_values) + " |",
        "| " + " | ".join("---" for _ in header_values) + " |",
    ]
    lines.extend("| " + " | ".join(_markdown_cell(value) for value in row) + " |" for row in rows)
    return lines


def _gate_value(gate: GateResult, value: float | int | str) -> str:
    if gate.gate_id in RATE_GATE_IDS and isinstance(value, (float, int)):
        return _percent(float(value))
    return str(value)


def _provenance_report(comparison: Stage3Comparison) -> EvaluationReport:
    return comparison.reports["rewrite"]


def _render_conclusion(comparison: Stage3Comparison) -> list[str]:
    rewrite = comparison.reports["rewrite"]
    status = "通过" if comparison.passed else "未通过"
    return [
        f"阶段 3 综合质量验收结论：**{status}**。",
        "",
        (
            f"最终 rewrite 模式：Recall@5 {_percent(rewrite.recall_at_5)}，"
            f"引用命中率 {_percent(rewrite.citation_hit_rate)}，"
            f"拒答准确率 {_percent(rewrite.refusal_accuracy)}。"
        ),
    ]


def _render_provenance(comparison: Stage3Comparison) -> list[str]:
    report = _provenance_report(comparison)
    provenance = report.provenance
    if provenance is None:
        raise ValueError("公开报告需要 1.1 溯源信息")
    return [
        f"- 数据集 SHA-256：`{report.dataset_sha256}`",
        f"- 运行 ID：`{provenance.run_id}`",
        f"- 知识库快照摘要：`{provenance.snapshot_sha256[:12]}`",
        f"- 文档数：{provenance.document_count}",
        f"- 片段数：{provenance.chunk_count}",
        f"- Top K：{report.top_k}",
        f"- 案例数：{report.case_count}",
    ]


def _render_environment(comparison: Stage3Comparison) -> list[str]:
    rows = []
    for key in SAFE_ENVIRONMENT_KEYS:
        values = [comparison.reports[mode].environment.get(key, "未记录") for mode in MODES]
        rows.append((f"`{key}`", *values))
    return _table(("公开字段", *MODES), rows)


def _render_overall_metrics(comparison: Stage3Comparison) -> list[str]:
    rows = []
    for mode in MODES:
        report = comparison.reports[mode]
        rows.append(
            (
                mode,
                _percent(report.recall_at_5),
                _percent(report.mrr_at_5),
                _percent(report.citation_hit_rate),
                _percent(report.refusal_accuracy),
                _milliseconds(report.latency_p50_ms),
                _milliseconds(report.latency_p95_ms),
            )
        )
    return _table(
        ("模式", "Recall@5", "MRR@5", "引用命中率", "拒答准确率", "P50", "P95"),
        rows,
    )


def _render_category_recall(comparison: Stage3Comparison) -> list[str]:
    rows = [
        (
            category,
            *(_percent(comparison.category_recall[mode].get(category, 0.0)) for mode in MODES),
        )
        for category in CATEGORY_ORDER
    ]
    return _table(("分类", *MODES), rows)


def _render_deltas(comparison: Stage3Comparison) -> list[str]:
    rows = []
    for mode in MODES:
        delta = comparison.metric_deltas[mode]
        rows.append(
            (
                mode,
                _signed_percent(delta.recall_at_5),
                _signed_percent(delta.mrr_at_5),
                _signed_percent(delta.citation_hit_rate),
                _signed_percent(delta.refusal_accuracy),
                _signed_milliseconds(delta.latency_p50_ms),
                _signed_milliseconds(delta.latency_p95_ms),
            )
        )
    return _table(
        ("模式", "Recall@5", "MRR@5", "引用命中率", "拒答准确率", "P50", "P95"),
        rows,
    )


def _render_gates(comparison: Stage3Comparison) -> list[str]:
    return _table(
        ("质量门", "状态", "实际值", "目标值", "说明"),
        (
            (
                gate.gate_id,
                STATUS_LABELS[gate.status],
                _gate_value(gate, gate.actual),
                _gate_value(gate, gate.target),
                gate.message,
            )
            for gate in comparison.gates
        ),
    )


def _render_stage3c_waiver(comparison: Stage3Comparison) -> list[str]:
    gate = next(gate for gate in comparison.gates if gate.gate_id == "stage3c.mrr_relative_gain")
    lines = [
        (
            f"3C MRR 相对提升原门槛为至少 {_gate_value(gate, gate.target)}，"
            f"本次实际 {_gate_value(gate, gate.actual)}。"
        )
    ]
    if gate.status == "waived" and gate.waiver is not None:
        lines.extend(
            [
                "",
                "**质量门未通过、已获风险豁免**。",
                "",
                f"- 批准日期：{gate.waiver.approved_on.isoformat()}",
                f"- 豁免原因：{gate.waiver.reason}",
                f"- 证据路径：`{gate.waiver.evidence.as_posix()}`",
                f"- 最低允许值：{_percent(gate.waiver.minimum_allowed)}",
            ]
        )
    elif gate.status == "passed":
        lines.extend(["", "本次达到原质量门，无需使用风险豁免。"])
    else:
        lines.extend(["", "本次未达到原质量门，且风险豁免不适用。"])
    lines.extend(
        [
            "",
            "严格边界：MRR 负增长或引用下降时豁免不适用；出现该情况必须停止验收并分析退化原因。",
        ]
    )
    return lines


def _render_failure_ids(comparison: Stage3Comparison) -> list[str]:
    groups = [(key, comparison.failure_case_ids.get(key, [])) for key in FAILURE_GROUP_ORDER]
    groups.extend(
        (key, values)
        for key, values in sorted(comparison.failure_case_ids.items())
        if key not in FAILURE_GROUP_ORDER
    )
    populated = [(key, values) for key, values in groups if values]
    if not populated:
        return ["无。"]
    return [
        f"- `{key}`：{', '.join(f'`{case_id}`' for case_id in values)}" for key, values in populated
    ]


def ensure_public_report_safe(markdown: str) -> None:
    lowered = markdown.casefold()
    if any(marker in lowered for marker in SENSITIVE_MARKERS):
        raise ValueError("公开报告包含敏感标记")


def render_stage3_markdown(
    comparison: Stage3Comparison,
    *,
    reproduce_command: str,
) -> str:
    sections: list[tuple[str, list[str]]] = [
        ("## 1. 验收结论", _render_conclusion(comparison)),
        ("## 2. 数据集与知识库快照", _render_provenance(comparison)),
        ("## 3. 执行环境", _render_environment(comparison)),
        ("## 4. 四模式总体指标", _render_overall_metrics(comparison)),
        ("## 5. 各分类 Recall@5", _render_category_recall(comparison)),
        ("## 6. 相对 vector 的提升或退化", _render_deltas(comparison)),
        ("## 7. 3A～3E 质量门结果", _render_gates(comparison)),
        ("## 8. 3C 风险豁免及适用边界", _render_stage3c_waiver(comparison)),
        ("## 9. 失败案例 ID", _render_failure_ids(comparison)),
        (
            "## 10. 最终推荐配置与纯向量回退配置",
            [
                f"- 最终推荐配置：`{comparison.recommended_mode}`。",
                f"- 纯向量回退配置：`{comparison.fallback_mode}`。",
                "- 回退说明：纯向量模式减少本地重排和问题改写依赖，适合故障隔离与保底运行。",
            ],
        ),
        (
            "## 11. 可重复执行命令",
            ["```powershell", reproduce_command, "```"],
        ),
        (
            "## 12. 已知风险与延迟说明",
            [
                "- MRR、P50 和 P95 用于观察质量与性能变化，不是 3E 的绝对质量门。",
                "- 真实模型与本地硬件负载会造成延迟波动，应结合多次运行趋势判断。",
                "- 3D 选择性问题改写与 `QUESTION_REWRITE_ERROR` 安全回退结论来自"
                "自动化测试证据，不从评估 JSON 指标推断。",
            ],
        ),
    ]
    lines = ["# 阶段 3 RAG 质量综合验收报告"]
    for heading, body in sections:
        lines.extend(["", heading, "", *body])
    markdown = "\n".join(lines) + "\n"
    ensure_public_report_safe(markdown)
    return markdown
